import os
import requests
import smtplib
import logging
from email.message import EmailMessage
from urllib.parse import urlparse
from dotenv import load_dotenv
from flask import Flask, jsonify, request, session, send_from_directory, abort
from werkzeug.utils import secure_filename
from flask_cors import CORS
from flask_migrate import Migrate, upgrade
from config import config
from models import db, User, Filament, FilamentUsage, Order, OrderItem, ProductProfile, PrintSession, OrderNote, CommunicationLog, Expense, Customer, CustomerRequest, CustomerFeedback, Printer, CustomerFile, PrinterConnection, BambuMaterial, PrintNotification, ScheduledPrint, AlertSettings
from authentication import EtsyOAuth, TokenManager, token_required
from etsy_api import EtsyAPI, OrderSyncManager, schedule_order_prints
from datetime import datetime, timedelta, timezone

# Configure secure logging
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
migrate = Migrate()

def create_app(config_name='development'):
    """Application factory"""
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(config[config_name])

    # Uploads
    app.config['UPLOAD_FOLDER'] = os.path.join(app.instance_path, 'uploads')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Configure session
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    CORS(
        app,
        resources={r"/api/*": {
            "origins": app.config['CORS_ORIGINS'],
            "allow_private_network": False  # CVE-2024-6221 mitigation
        }},
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization"],
    )
    
    # Schema management hooks (dev convenience / opt-in for prod)
    with app.app_context():
        if os.getenv('RUN_DB_UPGRADE') == '1':
            try:
                upgrade()
                logger.info("Applied migrations via RUN_DB_UPGRADE=1")
            except Exception as e:
                logger.warning(f"Migration upgrade failed: {type(e).__name__}")
        elif app.config.get('AUTO_DB_CREATE') or os.getenv('AUTO_DB_CREATE') == '1':
            db.create_all()
    
    # ==================== AUTH ROUTES ====================
    @app.route('/api/auth/login', methods=['GET'])
    def get_login_url():
        """Get Etsy OAuth login URL"""
        try:
            session.permanent = True
            url, state, code_verifier = EtsyOAuth.get_authorization_url()
            return jsonify({'auth_url': url, 'code_verifier': code_verifier}), 200
        except Exception as e:
            logger.exception("Exception in get_login_url")
            return jsonify({'error': 'Failed to generate login URL'}), 500
    
    @app.route('/api/auth/callback', methods=['POST'])
    def oauth_callback():
        try:
            code = request.json.get('code')
            code_verifier = request.json.get('code_verifier')
            if not code:
                return jsonify({'error': 'Missing authorization code'}), 400
            if not code_verifier:
                return jsonify({'error': 'Missing code_verifier'}), 400
            
            # Exchange code for token
            token_data = EtsyOAuth.exchange_code_for_token(code, code_verifier)
            access_token = token_data['access_token']
            refresh_token = token_data.get('refresh_token')
            expires_in = token_data.get('expires_in', 3600)
            
            # Get user info
            user_info = EtsyOAuth.get_user_info(access_token)
            etsy_user_id = str(user_info['user_id'])
            shop_id = user_info.get('shop_id')
            first_name = user_info.get('first_name', '')  # NEW
            
            # Get shop info if shop_id exists
            shop_name = None
            if shop_id:
                try:
                    shop_info = EtsyOAuth.get_shop_info(access_token, shop_id)
                    shop_name = shop_info.get('shop_name', '')  # NEW
                    print(f"DEBUG: Shop name: {shop_name}")
                except Exception as e:
                    print(f"DEBUG: Could not fetch shop info: {e}")
            
            # Use first_name or fallback to username
            username = first_name if first_name else f"etsy_user_{etsy_user_id}"
            
            # Check if user exists
            user = User.query.filter_by(etsy_user_id=etsy_user_id).first()
            
            if user:
                # Update existing user
                user.username = username  # Update name in case we got better info
                user.access_token = access_token
                user.refresh_token = refresh_token
                user.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                user.updated_at = datetime.now(timezone.utc)
                user.username = username  # NEW
                user.first_name = first_name  # NEW
                if shop_id:
                    user.shop_id = shop_id
                if shop_name:
                    user.shop_name = shop_name  # NEW
            else:
                # Create new user
                user = User(
                    etsy_user_id=etsy_user_id,
                    username=username,
                    first_name=first_name,  # NEW
                    access_token=access_token,
                    refresh_token=refresh_token,
                    token_expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                )
                if shop_id:
                    user.shop_id = shop_id
                if shop_name:
                    user.shop_name = shop_name  # NEW
                db.session.add(user)
            
            db.session.commit()
            
            # Create JWT token
            jwt_token = TokenManager.create_token(user.id)
            
            return jsonify({
                'success': True,
                'token': jwt_token,
                'user': {
                    'id': user.id,
                    'etsy_user_id': user.etsy_user_id,
                    'username': user.username,
                    'first_name': user.first_name,  # NEW
                    'shop_id': user.shop_id,
                    'shop_name': user.shop_name  # NEW
                }
            }), 200
            
        except Exception as e:
            logger.exception("Exception in oauth_callback")
            return jsonify({'error': 'Authentication failed'}), 500
    
    @app.route('/api/auth/logout', methods=['POST'])
    @token_required
    def logout():
        """Logout user"""
        # Token is invalidated on frontend by deletion
        return jsonify({'message': 'Successfully logged out'}), 200
    
    @app.route('/api/auth/user', methods=['GET'])
    @token_required
    def get_user():
        """Get current authenticated user"""
        return jsonify(request.user.to_dict()), 200
    
    # ==================== ORDER ROUTES ====================
    @app.route('/api/orders/sync', methods=['POST'])
    @token_required
    def sync_orders():
        """Sync orders from Etsy"""
        try:
            logger.info("sync_orders endpoint called")
            
            # Check if token needs refresh
            user = request.user
            logger.info(f"Processing sync for user: {user.etsy_user_id}")
            
            # Make token_expires_at timezone-aware if it isn't
            if user.token_expires_at:
                if user.token_expires_at.tzinfo is None:
                    # If naive, assume it's UTC
                    token_expires_at = user.token_expires_at.replace(tzinfo=timezone.utc)
                else:
                    token_expires_at = user.token_expires_at
                
                if token_expires_at <= datetime.now(timezone.utc):
                    logger.info("Token expired, refreshing")
                    # Refresh token
                    token_data = EtsyOAuth.refresh_access_token(user.refresh_token)
                    user.access_token = token_data['access_token']
                    user.refresh_token = token_data.get('refresh_token', user.refresh_token)
                    user.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data.get('expires_in', 3600))
                    db.session.commit()
                    logger.info("Token refreshed successfully")
            
            # Check if user has a shop_id
            if not user.shop_id:
                logger.warning("No shop_id found for user")
                return jsonify({'error': 'No shop associated with this account'}), 404
            
            logger.info("Initializing Etsy API")
            # Initialize Etsy API
            etsy_api = EtsyAPI(user.access_token)
            
            shop_id = user.shop_id
            logger.info(f"Starting order sync for shop_id: {shop_id}")
            
            # Sync orders
            result = OrderSyncManager.sync_orders_from_etsy(user, shop_id, etsy_api, months=6)
            logger.info(f"Sync result: {result.get('message', 'Completed')}")
            
            return jsonify(result), 200 if result['success'] else 500
        
        except Exception as e:
            # Log detailed error information securely on the server
            logger.exception("Exception in sync_orders")
            # Return generic error to client without exposing implementation details
            return jsonify({'error': 'An error occurred during order synchronization', 'success': False}), 500
    
    @app.route('/api/orders', methods=['GET'])
    @token_required
    def get_orders():
        """Get all orders for authenticated user with filters"""
        try:
            user = request.user
            status = request.args.get('status')
            prod_status = request.args.get('production_status')
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            product = request.args.get('product')
            min_total = request.args.get('min_total')
            max_total = request.args.get('max_total')
            
            query = Order.query.filter_by(user_id=user.id)
            if status:
                query = query.filter(Order.status == status)
            if prod_status:
                query = query.filter(Order.production_status == prod_status)
            if start_date:
                try:
                    start_dt = datetime.fromisoformat(start_date)
                    query = query.filter(Order.created_at >= start_dt)
                except ValueError:
                    # Invalid start_date format; ignore this filter and proceed without it
                    pass
            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date)
                    query = query.filter(Order.created_at <= end_dt)
                except ValueError:
                    # Invalid end_date format; ignore this filter and proceed without it
                    pass
            if min_total:
                try:
                    query = query.filter(Order.total_amount >= float(min_total))
                except ValueError:
                    # Invalid min_total value; ignore this filter and proceed without it
                    pass
            if max_total:
                try:
                    query = query.filter(Order.total_amount <= float(max_total))
                except ValueError:
                    # Invalid max_total value; ignore this filter and proceed without it
                    pass
            if product:
                query = query.join(Order.items).filter(OrderItem.title.ilike(f"%{product}%"))
            
            orders = query.order_by(Order.created_at.desc()).all()
            
            return jsonify({
                'orders': [order.to_dict() for order in orders],
                'total': len(orders)
            }), 200
        
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/orders/<order_id>', methods=['GET'])
    @token_required
    def get_order(order_id):
        """Get specific order"""
        try:
            user = request.user
            order = Order.query.filter_by(id=order_id, user_id=user.id).first()
            
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            
            return jsonify(order.to_dict()), 200
        
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/orders/bulk-actions', methods=['POST'])
    @token_required
    def bulk_order_actions():
        """Perform bulk actions on orders (mark shipped, update status, assign filament)"""
        try:
            current_user = request.user
            data = request.get_json() or {}
            order_ids = data.get('order_ids', [])
            action = data.get('action')

            if not order_ids or not isinstance(order_ids, list):
                return jsonify({'error': 'order_ids list is required'}), 400
            if not action:
                return jsonify({'error': 'action is required'}), 400

            orders = Order.query.filter(Order.user_id == current_user.id, Order.id.in_(order_ids)).all()
            if not orders:
                return jsonify({'error': 'No matching orders found'}), 404

            if action == 'mark_shipped':
                now = datetime.now(timezone.utc)
                for order in orders:
                    order.status = 'SHIPPED'
                    order.production_status = 'SHIPPED'
                    order.shipped_at = now
            elif action == 'update_status':
                new_status = data.get('status')
                if not new_status:
                    return jsonify({'error': 'status is required for update_status'}), 400
                for order in orders:
                    order.status = new_status
            elif action == 'assign_filament':
                for order in orders:
                    order.filament_assigned = True
            else:
                return jsonify({'error': f'Unsupported action {action}'}), 400

            db.session.commit()
            return jsonify({'orders': [order.to_dict() for order in orders], 'total': len(orders)}), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/orders/<int:order_id>/notes', methods=['GET', 'POST'])
    @token_required
    def order_notes(order_id):
        """List or add internal notes for an order"""
        try:
            current_user = request.user
            order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
            if not order:
                return jsonify({'error': 'Order not found'}), 404

            if request.method == 'GET':
                notes = OrderNote.query.filter_by(order_id=order_id).order_by(OrderNote.created_at.desc()).all()
                return jsonify({'notes': [n.to_dict() for n in notes], 'total': len(notes)}), 200

            data = request.get_json() or {}
            content = data.get('content')
            if not content:
                return jsonify({'error': 'Note content is required'}), 400
            note = OrderNote(order_id=order_id, user_id=current_user.id, content=content)
            db.session.add(note)
            db.session.commit()
            return jsonify(note.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/orders/<int:order_id>/communications', methods=['GET', 'POST'])
    @token_required
    def order_communications(order_id):
        """Customer communication log"""
        try:
            current_user = request.user
            order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
            if not order:
                return jsonify({'error': 'Order not found'}), 404

            if request.method == 'GET':
                logs = CommunicationLog.query.filter_by(order_id=order_id).order_by(CommunicationLog.created_at.desc()).all()
                return jsonify({'logs': [log.to_dict() for log in logs], 'total': len(logs)}), 200

            data = request.get_json() or {}
            message = data.get('message')
            if not message:
                return jsonify({'error': 'Message is required'}), 400
            log = CommunicationLog(
                order_id=order_id,
                user_id=current_user.id,
                direction=data.get('direction', 'outbound'),
                channel=data.get('channel', 'message'),
                message=message,
            )
            order.last_customer_contact_at = datetime.now(timezone.utc)
            db.session.add(log)
            db.session.commit()
            return jsonify(log.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    # ==================== CUSTOMER CRM ROUTES ====================
    @app.route('/api/customers', methods=['GET', 'POST'])
    @token_required
    def customers():
        """List or create customers"""
        try:
            current_user = request.user
            if request.method == 'GET':
                q = (request.args.get('q') or '').strip().lower()
                segment = (request.args.get('segment') or '').lower()

                query = Customer.query.filter_by(user_id=current_user.id)
                if q:
                    like = f"%{q}%"
                    query = query.filter(db.or_(Customer.email.ilike(like), Customer.name.ilike(like)))

                if segment:
                    if segment == 'vip':
                        query = query.filter(db.or_(Customer.total_spend >= 300, Customer.order_count >= 5))
                    elif segment == 'repeat':
                        query = query.filter(Customer.order_count >= 2, Customer.total_spend < 300)
                    elif segment == 'new':
                        query = query.filter(Customer.order_count == 1)

                customers = query.order_by(Customer.last_order_at.desc().nullslast()).all()
                return jsonify({'customers': [c.to_dict() for c in customers], 'total': len(customers)}), 200

            data = request.get_json() or {}
            customer = Customer(
                user_id=current_user.id,
                name=data.get('name'),
                email=data.get('email'),
                phone=data.get('phone'),
                notes=data.get('notes')
            )
            db.session.add(customer)
            db.session.commit()
            return jsonify(customer.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/customers/<int:customer_id>', methods=['GET', 'PUT'])
    @token_required
    def customer_detail(customer_id):
        """Fetch or update a single customer"""
        try:
            current_user = request.user
            customer = Customer.query.filter_by(id=customer_id, user_id=current_user.id).first()
            if not customer:
                return jsonify({'error': 'Customer not found'}), 404

            if request.method == 'GET':
                orders = Order.query.filter_by(user_id=current_user.id, customer_id=customer.id).order_by(Order.created_at.desc()).all()
                return jsonify({
                    'customer': customer.to_dict(),
                    'orders': [o.to_dict() for o in orders]
                }), 200

            data = request.get_json() or {}
            for field in ['name', 'email', 'phone', 'notes']:
                if field in data:
                    setattr(customer, field, data[field])
            db.session.commit()
            return jsonify(customer.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/customers/segments', methods=['GET'])
    @token_required
    def customer_segments():
        """Return counts per customer segment"""
        try:
            current_user = request.user
            customers = Customer.query.filter_by(user_id=current_user.id).all()
            summary = {'VIP': 0, 'repeat': 0, 'new': 0, 'prospect': 0}
            for c in customers:
                summary[c.segment()] = summary.get(c.segment(), 0) + 1
            return jsonify(summary), 200
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/customers/<int:customer_id>/requests', methods=['GET', 'POST'])
    @token_required
    def customer_requests(customer_id):
        """List or create custom product requests"""
        try:
            current_user = request.user
            customer = Customer.query.filter_by(id=customer_id, user_id=current_user.id).first()
            if not customer:
                return jsonify({'error': 'Customer not found'}), 404

            if request.method == 'GET':
                requests_data = CustomerRequest.query.filter_by(user_id=current_user.id, customer_id=customer_id).order_by(CustomerRequest.created_at.desc()).all()
                return jsonify({'requests': [r.to_dict() for r in requests_data], 'total': len(requests_data)}), 200

            data = request.get_json() or {}
            req = CustomerRequest(
                user_id=current_user.id,
                customer_id=customer_id,
                title=data.get('title', 'Custom request'),
                description=data.get('description'),
                status=data.get('status', 'open'),
                priority=data.get('priority', 'normal'),
                desired_by=datetime.fromisoformat(data['desired_by']) if data.get('desired_by') else None
            )
            db.session.add(req)
            db.session.commit()
            return jsonify(req.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/customer-requests/<int:request_id>', methods=['PATCH'])
    @token_required
    def update_customer_request(request_id):
        """Update a custom request"""
        try:
            current_user = request.user
            req = CustomerRequest.query.filter_by(id=request_id, user_id=current_user.id).first()
            if not req:
                return jsonify({'error': 'Request not found'}), 404
            data = request.get_json() or {}
            for field in ['title', 'description', 'status', 'priority']:
                if field in data:
                    setattr(req, field, data[field])
            if 'desired_by' in data:
                req.desired_by = datetime.fromisoformat(data['desired_by']) if data['desired_by'] else None
            db.session.commit()
            return jsonify(req.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/customers/<int:customer_id>/feedback', methods=['GET', 'POST'])
    @token_required
    def customer_feedback(customer_id):
        """List or create feedback entries"""
        try:
            current_user = request.user
            customer = Customer.query.filter_by(id=customer_id, user_id=current_user.id).first()
            if not customer:
                return jsonify({'error': 'Customer not found'}), 404

            if request.method == 'GET':
                feedback = CustomerFeedback.query.filter_by(user_id=current_user.id, customer_id=customer_id).order_by(CustomerFeedback.created_at.desc()).all()
                return jsonify({'feedback': [f.to_dict() for f in feedback], 'total': len(feedback)}), 200

            data = request.get_json() or {}
            fb = CustomerFeedback(
                user_id=current_user.id,
                customer_id=customer_id,
                order_id=data.get('order_id'),
                rating=data.get('rating'),
                comment=data.get('comment'),
                source=data.get('source', 'manual')
            )
            db.session.add(fb)
            db.session.commit()
            return jsonify(fb.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/orders/<int:order_id>/photo', methods=['POST'])
    @token_required
    def upload_order_photo(order_id):
        """Upload a finished product photo and attach to order"""
        try:
            current_user = request.user
            order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
            if not order:
                return jsonify({'error': 'Order not found'}), 404

            if 'photo' not in request.files:
                return jsonify({'error': 'No photo file provided'}), 400

            file = request.files['photo']
            if file.filename == '':
                return jsonify({'error': 'Empty filename'}), 400

            if not file.filename:
                return jsonify({'error': 'Invalid filename'}), 400
            
            filename = secure_filename(file.filename)
            if not filename:
                return jsonify({'error': 'Invalid filename'}), 400
            
            # Ensure filename is safe and doesn't contain path separators
            if '/' in filename or '\\' in filename or filename.startswith('.'):
                return jsonify({'error': 'Invalid filename'}), 400
            
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            final_name = f"order_{order_id}_{timestamp}_{filename}"
            
            # Build path and validate it stays within upload folder
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], final_name)
            upload_folder = os.path.abspath(app.config['UPLOAD_FOLDER'])
            resolved_path = os.path.abspath(save_path)
            
            if not resolved_path.startswith(upload_folder + os.sep):
                return jsonify({'error': 'Invalid file path'}), 400
            
            file.save(resolved_path)

            public_url = f"/uploads/{final_name}"
            order.photo_url = public_url
            db.session.commit()
            return jsonify({'photo_url': public_url}), 201
        except Exception as e:
            db.session.rollback()
            print(f"Error uploading order photo: {e}")
            return jsonify({'error': 'Failed to upload photo'}), 500

    @app.route('/api/orders/<int:order_id>/shipping-label', methods=['POST', 'PUT'])
    @token_required
    def shipping_label(order_id):
        """Stub endpoint to store shipping label metadata"""
        try:
            current_user = request.user
            order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
            if not order:
                return jsonify({'error': 'Order not found'}), 404

            data = request.get_json() or {}
            order.shipping_provider = data.get('provider', order.shipping_provider or 'manual')
            order.shipping_label_status = data.get('status', order.shipping_label_status or 'CREATED')
            order.shipping_label_url = data.get('label_url', order.shipping_label_url)
            order.tracking_number = data.get('tracking_number', order.tracking_number)

            # If label purchased, mark shipped_at optionally
            if data.get('status') == 'PURCHASED' and not order.shipped_at:
                order.shipped_at = datetime.now(timezone.utc)

            db.session.commit()
            return jsonify(order.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    # ==================== FILAMENT ROUTES ====================
    @app.route('/api/filaments', methods=['GET'])
    @token_required
    def get_filaments():
        """Get all filaments for authenticated user"""
        try:
            user = request.user
            filaments = Filament.query.filter_by(user_id=user.id).all()
            
            return jsonify({
                'filaments': [filament.to_dict() for filament in filaments],
                'total': len(filaments)
            }), 200
        
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/filaments', methods=['POST'])
    @token_required
    def create_filament():
        """Create a new filament entry"""
        try:
            user = request.user
            data = request.json
            
            filament = Filament(
                user_id=user.id,
                color=data.get('color'),
                material=data.get('material'),
                initial_amount=float(data.get('initial_amount', 0)),
                current_amount=float(data.get('current_amount', 0)),
                unit=data.get('unit', 'g'),
                cost_per_gram=float(data.get('cost_per_gram', 0)) if data.get('cost_per_gram') else None
            )
            
            db.session.add(filament)
            db.session.commit()
            
            return jsonify(filament.to_dict()), 201
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/filaments/<filament_id>', methods=['PUT'])
    @token_required
    def update_filament(filament_id):
        """Update filament information"""
        try:
            user = request.user
            filament = Filament.query.filter_by(id=filament_id, user_id=user.id).first()
            
            if not filament:
                return jsonify({'error': 'Filament not found'}), 404
            
            data = request.json
            
            if 'color' in data:
                filament.color = data['color']
            if 'material' in data:
                filament.material = data['material']
            if 'current_amount' in data:
                filament.current_amount = float(data['current_amount'])
            if 'initial_amount' in data:
                filament.initial_amount = float(data['initial_amount'])
            if 'cost_per_gram' in data:
                filament.cost_per_gram = float(data['cost_per_gram']) if data['cost_per_gram'] else None
            if 'low_stock_threshold' in data:
                filament.low_stock_threshold = float(data['low_stock_threshold']) if data['low_stock_threshold'] else 100.0
            
            filament.updated_at = datetime.utcnow()
            db.session.commit()
            
            return jsonify(filament.to_dict()), 200
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/filaments/<filament_id>', methods=['DELETE'])
    @token_required
    def delete_filament(filament_id):
        """Delete a filament entry"""
        try:
            user = request.user
            filament = Filament.query.filter_by(id=filament_id, user_id=user.id).first()
            
            if not filament:
                return jsonify({'error': 'Filament not found'}), 404
            
            db.session.delete(filament)
            db.session.commit()
            
            return jsonify({'message': 'Filament deleted successfully'}), 200
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    # ==================== FILAMENT USAGE ROUTES ====================
    @app.route('/api/filament-usage', methods=['POST'])
    @token_required
    def record_filament_usage():
        """Record filament usage (subtract from current amount)"""
        try:
            user = request.user
            data = request.json
            
            filament_id = data.get('filament_id')
            amount_used = float(data.get('amount_used', 0))
            order_id = data.get('order_id')
            description = data.get('description')
            
            # Get filament
            filament = Filament.query.filter_by(id=filament_id, user_id=user.id).first()
            if not filament:
                return jsonify({'error': 'Filament not found'}), 404
            
            # Check order if provided
            if order_id:
                order = Order.query.filter_by(id=order_id, user_id=user.id).first()
                if not order:
                    return jsonify({'error': 'Order not found'}), 404
            
            # Record usage
            usage = FilamentUsage(
                filament_id=filament_id,
                order_id=order_id,
                amount_used=amount_used,
                description=description
            )
            
            # Subtract from current amount
            filament.current_amount -= amount_used
            filament.current_amount = max(0, filament.current_amount)  # Don't go negative
            filament.updated_at = datetime.utcnow()
            
            # Update order if provided
            if order_id:
                order.total_filament_used += amount_used
                order.filament_assigned = True
            
            db.session.add(usage)
            db.session.commit()
            
            return jsonify({
                'usage': usage.to_dict(),
                'filament': filament.to_dict(),
                'message': 'Filament usage recorded'
            }), 201
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/filament-usage/order/<order_id>', methods=['GET'])
    @token_required
    def get_order_filament_usage(order_id):
        """Get all filament usage for a specific order"""
        try:
            user = request.user
            order = Order.query.filter_by(id=order_id, user_id=user.id).first()
            
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            
            usages = FilamentUsage.query.filter_by(order_id=order_id).all()
            
            return jsonify({
                'usages': [usage.to_dict() for usage in usages],
                'total_filament_used': order.total_filament_used
            }), 200
        
        except Exception as e:
            print(f"Error getting filament usage: {e}")
            return jsonify({'error': 'Failed to get filament usage'}), 500
    
    # ==================== PRODUCT PROFILE ROUTES ====================
    @app.route('/api/product-profiles', methods=['GET'])
    @token_required
    def get_product_profiles():
        """Get all product profiles for authenticated user"""
        try:
            user = request.user
            profiles = ProductProfile.query.filter_by(user_id=user.id).all()
            
            return jsonify({
                'profiles': [profile.to_dict() for profile in profiles],
                'total': len(profiles)
            }), 200
        
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/product-profiles', methods=['POST'])
    @token_required
    def create_product_profile():
        """Create a new product profile"""
        try:
            user = request.user
            data = request.json
            
            profile = ProductProfile(
                user_id=user.id,
                product_name=data.get('product_name'),
                description=data.get('description'),
                standard_filament_amount=float(data.get('standard_filament_amount', 0)),
                preferred_material=data.get('preferred_material'),
                preferred_color=data.get('preferred_color'),
                print_time_minutes=int(data.get('print_time_minutes')) if data.get('print_time_minutes') else None,
                notes=data.get('notes'),
                category=data.get('category'),
                nozzle_temp_c=data.get('nozzle_temp_c'),
                bed_temp_c=data.get('bed_temp_c'),
                print_speed_mms=data.get('print_speed_mms'),
                support_settings=data.get('support_settings'),
                infill_percent=float(data['infill_percent']) if data.get('infill_percent') else None,
                layer_height_mm=float(data['layer_height_mm']) if data.get('layer_height_mm') else None,
                material_cost=float(data['material_cost']) if data.get('material_cost') else None,
                labor_minutes=int(data['labor_minutes']) if data.get('labor_minutes') else None,
                overhead_cost=float(data['overhead_cost']) if data.get('overhead_cost') else None,
                target_margin_pct=float(data['target_margin_pct']) if data.get('target_margin_pct') else None
            )
            
            db.session.add(profile)
            db.session.commit()
            
            return jsonify(profile.to_dict()), 201
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/product-profiles/<profile_id>', methods=['PUT'])
    @token_required
    def update_product_profile(profile_id):
        """Update product profile"""
        try:
            user = request.user
            profile = ProductProfile.query.filter_by(id=profile_id, user_id=user.id).first()
            
            if not profile:
                return jsonify({'error': 'Product profile not found'}), 404
            
            data = request.json
            
            if 'product_name' in data:
                profile.product_name = data['product_name']
            if 'description' in data:
                profile.description = data['description']
            if 'standard_filament_amount' in data:
                profile.standard_filament_amount = float(data['standard_filament_amount'])
            if 'preferred_material' in data:
                profile.preferred_material = data['preferred_material']
            if 'preferred_color' in data:
                profile.preferred_color = data['preferred_color']
            if 'print_time_minutes' in data:
                profile.print_time_minutes = int(data['print_time_minutes']) if data['print_time_minutes'] else None
            if 'notes' in data:
                profile.notes = data['notes']
            if 'category' in data:
                profile.category = data['category']
            if 'nozzle_temp_c' in data:
                profile.nozzle_temp_c = data['nozzle_temp_c']
            if 'bed_temp_c' in data:
                profile.bed_temp_c = data['bed_temp_c']
            if 'print_speed_mms' in data:
                profile.print_speed_mms = data['print_speed_mms']
            if 'support_settings' in data:
                profile.support_settings = data['support_settings']
            if 'infill_percent' in data:
                profile.infill_percent = float(data['infill_percent']) if data['infill_percent'] is not None else None
            if 'layer_height_mm' in data:
                profile.layer_height_mm = float(data['layer_height_mm']) if data['layer_height_mm'] is not None else None
            if 'material_cost' in data:
                profile.material_cost = float(data['material_cost']) if data['material_cost'] is not None else None
            if 'labor_minutes' in data:
                profile.labor_minutes = int(data['labor_minutes']) if data['labor_minutes'] is not None else None
            if 'overhead_cost' in data:
                profile.overhead_cost = float(data['overhead_cost']) if data['overhead_cost'] is not None else None
            if 'target_margin_pct' in data:
                profile.target_margin_pct = float(data['target_margin_pct']) if data['target_margin_pct'] is not None else None
            
            profile.updated_at = datetime.utcnow()
            db.session.commit()
            
            return jsonify(profile.to_dict()), 200
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/product-profiles/<profile_id>', methods=['DELETE'])
    @token_required
    def delete_product_profile(profile_id):
        """Delete a product profile"""
        try:
            user = request.user
            profile = ProductProfile.query.filter_by(id=profile_id, user_id=user.id).first()
            
            if not profile:
                return jsonify({'error': 'Product profile not found'}), 404
            
            db.session.delete(profile)
            db.session.commit()
            
            return jsonify({'message': 'Product profile deleted successfully'}), 200
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/orders/<order_id>/auto-assign-filament', methods=['POST'])
    @token_required
    def auto_assign_filament(order_id):
        """Automatically assign filament to order based on product profiles"""
        try:
            user = request.user
            order = Order.query.filter_by(id=order_id, user_id=user.id).first()
            
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            
            # Get all product profiles
            profiles = ProductProfile.query.filter_by(user_id=user.id).all()
            profile_map = {p.product_name.lower(): p for p in profiles}
            
            total_assigned = 0
            assignments = []
            
            # Match order items to product profiles
            for item in order.items:
                item_title_lower = item.title.lower()
                matched_profile = None
                
                # Try exact match first
                if item_title_lower in profile_map:
                    matched_profile = profile_map[item_title_lower]
                else:
                    # Try partial match
                    for profile_name, profile in profile_map.items():
                        if profile_name in item_title_lower or item_title_lower in profile_name:
                            matched_profile = profile
                            break
                
                if matched_profile:
                    # Calculate total filament needed
                    quantity = item.quantity or 1
                    filament_needed = matched_profile.standard_filament_amount * quantity
                    
                    # Find matching filament
                    filament = Filament.query.filter_by(
                        user_id=user.id,
                        material=matched_profile.preferred_material,
                        color=matched_profile.preferred_color
                    ).first()
                    
                    if not filament:
                        # Try to find any filament with matching material
                        filament = Filament.query.filter_by(
                            user_id=user.id,
                            material=matched_profile.preferred_material
                        ).filter(Filament.current_amount >= filament_needed).first()
                    
                    if filament and filament.current_amount >= filament_needed:
                        # Record usage
                        usage = FilamentUsage(
                            filament_id=filament.id,
                            order_id=order.id,
                            amount_used=filament_needed,
                            description=f"Auto-assigned for {item.title} (x{quantity})"
                        )
                        
                        # Update filament
                        filament.current_amount -= filament_needed
                        filament.updated_at = datetime.utcnow()
                        
                        db.session.add(usage)
                        total_assigned += filament_needed
                        
                        assignments.append({
                            'item': item.title,
                            'quantity': quantity,
                            'filament': f"{filament.material} - {filament.color}",
                            'amount_used': filament_needed
                        })
            
            if total_assigned > 0:
                # Update order
                order.total_filament_used = total_assigned
                order.filament_assigned = True
                db.session.commit()
                
                return jsonify({
                    'success': True,
                    'total_assigned': total_assigned,
                    'assignments': assignments,
                    'message': f'Successfully assigned {total_assigned}g of filament'
                }), 200
            else:
                return jsonify({
                    'success': False,
                    'message': 'No matching product profiles or insufficient filament stock'
                }), 400
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    # ==================== PRINTER ROUTES ====================
    @app.route('/api/printers', methods=['GET', 'POST'])
    @token_required
    def printers():
        """List or create printers"""
        try:
            current_user = request.user
            if request.method == 'GET':
                printers = Printer.query.filter_by(user_id=current_user.id).order_by(Printer.name.asc()).all()
                return jsonify({'printers': [p.to_dict() for p in printers], 'total': len(printers)}), 200

            data = request.get_json() or {}
            name = data.get('name')
            if not name:
                return jsonify({'error': 'name is required'}), 400
            printer = Printer(
                user_id=current_user.id,
                name=name,
                model=data.get('model'),
                location=data.get('location'),
                status=data.get('status', 'IDLE'),
                notes=data.get('notes'),
                maintenance_interval_days=data.get('maintenance_interval_days', 30),
                last_maintenance_at=datetime.fromisoformat(data['last_maintenance_at']) if data.get('last_maintenance_at') else None
            )
            db.session.add(printer)
            db.session.commit()
            return jsonify(printer.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/printers/<int:printer_id>', methods=['GET', 'PUT'])
    @token_required
    def printer_detail(printer_id):
        """Fetch or update printer"""
        try:
            current_user = request.user
            printer = Printer.query.filter_by(id=printer_id, user_id=current_user.id).first()
            if not printer:
                return jsonify({'error': 'Printer not found'}), 404

            if request.method == 'GET':
                return jsonify(printer.to_dict()), 200

            data = request.get_json() or {}
            for field in ['name', 'model', 'location', 'status', 'notes', 'maintenance_interval_days']:
                if field in data:
                    setattr(printer, field, data[field])
            if 'last_maintenance_at' in data:
                printer.last_maintenance_at = datetime.fromisoformat(data['last_maintenance_at']) if data['last_maintenance_at'] else None
            db.session.commit()
            return jsonify(printer.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/printers/<int:printer_id>/assign-orders', methods=['POST'])
    @token_required
    def assign_orders_to_printer(printer_id):
        """Assign multiple orders to a printer"""
        try:
            current_user = request.user
            printer = Printer.query.filter_by(id=printer_id, user_id=current_user.id).first()
            if not printer:
                return jsonify({'error': 'Printer not found'}), 404

            data = request.get_json() or {}
            order_ids = data.get('order_ids', [])
            if not order_ids:
                return jsonify({'error': 'order_ids is required'}), 400

            orders = Order.query.filter(Order.id.in_(order_ids), Order.user_id == current_user.id).all()
            for order in orders:
                order.printer_id = printer.id
            db.session.commit()
            return jsonify({'assigned_orders': [o.id for o in orders], 'printer': printer.to_dict()}), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/printers/utilization', methods=['GET'])
    @token_required
    def printer_utilization():
        """Aggregate printer utilization metrics"""
        try:
            current_user = request.user
            printers = Printer.query.filter_by(user_id=current_user.id).all()
            summary = []
            now = datetime.now(timezone.utc)
            seven_days_ago = now - timedelta(days=7)
            for printer in printers:
                orders = Order.query.filter_by(user_id=current_user.id, printer_id=printer.id).all()
                total_jobs = len(orders)
                total_minutes = sum((o.actual_print_time or o.estimated_print_time or 0) for o in orders)
                recent_minutes = sum((o.actual_print_time or o.estimated_print_time or 0) for o in orders if o.created_at and (o.created_at if o.created_at.tzinfo else o.created_at.replace(tzinfo=timezone.utc)) >= seven_days_ago)
                summary.append({
                    'printer': printer.to_dict(),
                    'total_jobs': total_jobs,
                    'total_minutes': total_minutes,
                    'recent_7d_minutes': recent_minutes
                })
            return jsonify({'utilization': summary}), 200
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    @app.route('/api/printers/maintenance', methods=['GET'])
    @token_required
    def printer_maintenance():
        """List maintenance schedule and due printers"""
        try:
            current_user = request.user
            printers = Printer.query.filter_by(user_id=current_user.id).all()
            now = datetime.now(timezone.utc)
            data = []
            for p in printers:
                next_due = p.next_maintenance_due()
                data.append({
                    'printer': p.to_dict(),
                    'maintenance_due': bool(next_due and next_due <= now),
                    'next_maintenance_at': next_due.isoformat() if next_due else None
                })
            return jsonify({'maintenance': data}), 200
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500

    # ==================== ANALYTICS ROUTES ====================
    @app.route('/api/analytics/summary', methods=['GET'])
    @token_required
    def get_analytics_summary():
        """Get overall analytics summary"""
        try:
            user = request.user
            
            # Get all orders
            orders = Order.query.filter_by(user_id=user.id).all()
            
            # Calculate totals
            total_orders = len(orders)
            total_revenue = sum(order.total_amount or 0 for order in orders)
            
            # Calculate filament costs
            total_filament_cost = 0
            for order in orders:
                if order.total_filament_used > 0:
                    # Get filament usage for this order
                    usages = FilamentUsage.query.filter_by(order_id=order.id).all()
                    for usage in usages:
                        filament = Filament.query.get(usage.filament_id)
                        if filament and filament.cost_per_gram:
                            total_filament_cost += usage.amount_used * filament.cost_per_gram
            
            # Calculate profit
            # Expenses
            expenses = Expense.query.filter_by(user_id=user.id).all()
            total_expenses = sum(e.amount or 0 for e in expenses)

            total_profit = total_revenue - total_filament_cost - total_expenses
            profit_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
            
            # Average order value
            avg_order_value = total_revenue / total_orders if total_orders > 0 else 0
            
            # Orders by status
            orders_by_status = {}
            for order in orders:
                status = order.status
                orders_by_status[status] = orders_by_status.get(status, 0) + 1
            
            # Recent orders (last 30 days)
            thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
            recent_orders = []
            for o in orders:
                if o.created_at:
                    # Make timezone-aware if naive
                    order_date = o.created_at if o.created_at.tzinfo else o.created_at.replace(tzinfo=timezone.utc)
                    if order_date >= thirty_days_ago:
                        recent_orders.append(o)
            recent_revenue = sum(order.total_amount or 0 for order in recent_orders)
            
            return jsonify({
                'total_orders': total_orders,
                'total_revenue': round(total_revenue, 2),
                'total_filament_cost': round(total_filament_cost, 2),
                'total_expenses': round(total_expenses, 2),
                'total_profit': round(total_profit, 2),
                'profit_margin': round(profit_margin, 2),
                'avg_order_value': round(avg_order_value, 2),
                'orders_by_status': orders_by_status,
                'recent_orders_count': len(recent_orders),
                'recent_revenue': round(recent_revenue, 2)
            }), 200
        
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/analytics/revenue-trends', methods=['GET'])
    @token_required
    def get_revenue_trends():
        """Get revenue trends over time including expenses"""
        try:
            user = request.user
            period = request.args.get('period', 'daily')  # daily, weekly, monthly
            
            orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at).all()
            expenses = Expense.query.filter_by(user_id=user.id).all()
            
            trends = {}
            
            def period_key(dt: datetime):
                if period == 'daily':
                    return dt.strftime('%Y-%m-%d')
                elif period == 'weekly':
                    week_start = dt - timedelta(days=dt.weekday())
                    return week_start.strftime('%Y-%m-%d')
                else:
                    return dt.strftime('%Y-%m')
            
            for order in orders:
                if not order.created_at or not order.total_amount:
                    continue
                key = period_key(order.created_at)
                trends.setdefault(key, {'period': key, 'revenue': 0, 'orders': 0, 'profit': 0, 'filament_cost': 0, 'expenses': 0})
                trends[key]['revenue'] += order.total_amount
                trends[key]['orders'] += 1
                usages = FilamentUsage.query.filter_by(order_id=order.id).all()
                for usage in usages:
                    filament = Filament.query.get(usage.filament_id)
                    if filament and filament.cost_per_gram:
                        cost = usage.amount_used * filament.cost_per_gram
                        trends[key]['filament_cost'] += cost
                trends[key]['profit'] = trends[key]['revenue'] - trends[key]['filament_cost']
            
            for exp in expenses:
                if not exp.expense_date:
                    continue
                key = period_key(exp.expense_date)
                trends.setdefault(key, {'period': key, 'revenue': 0, 'orders': 0, 'profit': 0, 'filament_cost': 0, 'expenses': 0})
                trends[key]['expenses'] += exp.amount or 0
                trends[key]['profit'] = trends[key]['revenue'] - trends[key]['filament_cost'] - trends[key]['expenses']
            
            trends_list = sorted(trends.values(), key=lambda x: x['period'])
            for trend in trends_list:
                trend['revenue'] = round(trend['revenue'], 2)
                trend['profit'] = round(trend['profit'], 2)
                trend['filament_cost'] = round(trend['filament_cost'], 2)
                trend['expenses'] = round(trend.get('expenses', 0), 2)
            
            return jsonify({'period': period, 'trends': trends_list}), 200
        
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/analytics/product-performance', methods=['GET'])
    @token_required
    def get_product_performance():
        """Get product performance metrics"""
        try:
            user = request.user
            
            # Get all orders with items
            orders = Order.query.filter_by(user_id=user.id).all()
            
            # Track products
            products = {}
            
            for order in orders:
                for item in order.items:
                    product_key = item.title
                    
                    if product_key not in products:
                        products[product_key] = {
                            'product_name': product_key,
                            'total_quantity': 0,
                            'total_revenue': 0,
                            'order_count': 0,
                            'avg_price': 0,
                            'material_cost': 0,
                            'overhead_cost': 0,
                            'labor_minutes': 0,
                            'profit': 0
                        }
                    
                    products[product_key]['total_quantity'] += item.quantity or 1
                    products[product_key]['total_revenue'] += (item.price or 0) * (item.quantity or 1)
                    products[product_key]['order_count'] += 1

                    # Cost from product profile if exists
                    profile = ProductProfile.query.filter_by(user_id=user.id, product_name=item.title).first()
                    if profile:
                        qty = item.quantity or 1
                        material_cost = (profile.material_cost or 0) * qty
                        overhead_cost = (profile.overhead_cost or 0) * qty
                        products[product_key]['material_cost'] += material_cost
                        products[product_key]['overhead_cost'] += overhead_cost
                        products[product_key]['labor_minutes'] += (profile.labor_minutes or 0) * qty
                        products[product_key]['profit'] = products[product_key]['total_revenue'] - products[product_key]['material_cost'] - products[product_key]['overhead_cost']
            
            # Calculate averages and round
            products_list = []
            for product in products.values():
                product['avg_price'] = product['total_revenue'] / product['total_quantity'] if product['total_quantity'] > 0 else 0
                product['total_revenue'] = round(product['total_revenue'], 2)
                product['avg_price'] = round(product['avg_price'], 2)
                product['material_cost'] = round(product['material_cost'], 2)
                product['overhead_cost'] = round(product['overhead_cost'], 2)
                product['profit'] = round(product['profit'], 2)
                products_list.append(product)
            
            # Sort by revenue
            products_list.sort(key=lambda x: x['total_revenue'], reverse=True)
            
            return jsonify({
                'products': products_list,
                'total_products': len(products_list)
            }), 200
        
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    # ==================== PRODUCTION QUEUE ROUTES ====================
    @app.route('/api/production/queue', methods=['GET'])
    @token_required
    def get_production_queue():
        """Get production queue sorted by priority"""
        try:
            current_user = request.user
            # Get orders in production (not yet shipped)
            orders = Order.query.filter_by(user_id=current_user.id).filter(
                Order.production_status.in_(['QUEUED', 'PRINTING', 'PRINTED', 'FAILED'])
            ).order_by(Order.priority.asc(), Order.created_at.asc()).all()
            
            return jsonify({
                'orders': [order.to_dict() for order in orders],
                'total': len(orders)
            }), 200
        
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/orders/<int:order_id>/production-status', methods=['PUT'])
    @token_required
    def update_production_status(order_id):
        """Update order production status"""
        try:
            current_user = request.user
            order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            
            data = request.get_json()
            new_status = data.get('production_status')
            
            if new_status not in ['QUEUED', 'PRINTING', 'PRINTED', 'SHIPPED', 'FAILED']:
                return jsonify({'error': 'Invalid status'}), 400
            
            order.production_status = new_status
            
            # Track timestamps
            if new_status == 'PRINTING' and not order.print_started_at:
                order.print_started_at = datetime.now(timezone.utc)
            elif new_status == 'PRINTED' and not order.print_completed_at:
                order.print_completed_at = datetime.now(timezone.utc)
                # Calculate actual print time
                if order.print_started_at:
                    delta = order.print_completed_at - order.print_started_at
                    order.actual_print_time = int(delta.total_seconds() / 60)
            elif new_status == 'FAILED':
                order.print_failures_count = (order.print_failures_count or 0) + 1
            
            # Update notes if provided
            if 'print_notes' in data:
                order.print_notes = data['print_notes']
            
            db.session.commit()
            return jsonify(order.to_dict()), 200
        
        except Exception as e:
            db.session.rollback()
            print(f"Error updating shipping label: {e}")
            return jsonify({'error': 'Failed to update shipping label'}), 500

    @app.route('/api/orders/<int:order_id>/priority', methods=['PUT'])
    @token_required
    def update_order_priority(order_id):
        """Update order priority"""
        try:
            current_user = request.user
            order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            
            data = request.get_json()
            priority = data.get('priority')
            
            if not priority or priority < 1 or priority > 5:
                return jsonify({'error': 'Priority must be between 1 (urgent) and 5 (backlog)'}), 400
            
            order.priority = priority
            db.session.commit()
            return jsonify(order.to_dict()), 200
        
        except Exception as e:
            db.session.rollback()
            print(f"Error updating order priority: {e}")
            return jsonify({'error': 'Failed to update priority'}), 500

    @app.route('/api/orders/<int:order_id>/print-time', methods=['PUT'])
    @token_required
    def update_print_time(order_id):
        """Update estimated print time"""
        try:
            current_user = request.user
            order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
            if not order:
                return jsonify({'error': 'Order not found'}), 404
            
            data = request.get_json()
            estimated_time = data.get('estimated_print_time')
            
            if estimated_time is not None:
                order.estimated_print_time = estimated_time
            
            db.session.commit()
            return jsonify(order.to_dict()), 200
        
        except Exception as e:
            db.session.rollback()
            print(f"Error updating print time: {e}")
            return jsonify({'error': 'Failed to update print time'}), 500

    @app.route('/api/print-sessions', methods=['GET', 'POST'])
    @token_required
    def manage_print_sessions():
        """Get all print sessions or create a new one"""
        try:
            current_user = request.user
            if request.method == 'GET':
                sessions = PrintSession.query.filter_by(user_id=current_user.id).order_by(
                    PrintSession.created_at.desc()
                ).all()
                return jsonify({
                    'sessions': [session.to_dict() for session in sessions],
                    'total': len(sessions)
                }), 200
            
            elif request.method == 'POST':
                data = request.get_json()
                name = data.get('name')
                order_ids = data.get('order_ids', [])
                
                if not name:
                    return jsonify({'error': 'Session name is required'}), 400
                
                # Create session
                session = PrintSession(
                    user_id=current_user.id,
                    name=name,
                    notes=data.get('notes', '')
                )
                db.session.add(session)
                db.session.flush()  # Get session ID
                
                # Assign orders to session
                total_estimated = 0
                for order_id in order_ids:
                    order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
                    if order:
                        order.print_session_id = session.id
                        if order.estimated_print_time:
                            total_estimated += order.estimated_print_time
                
                session.total_estimated_time = total_estimated
                db.session.commit()
                
                return jsonify(session.to_dict()), 201
        
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/print-sessions/<int:session_id>', methods=['GET', 'PUT', 'DELETE'])
    @token_required
    def manage_print_session(session_id):
        """Get, update, or delete a specific print session"""
        try:
            current_user = request.user
            session = PrintSession.query.filter_by(id=session_id, user_id=current_user.id).first()
            if not session:
                return jsonify({'error': 'Print session not found'}), 404
            
            if request.method == 'GET':
                session_data = session.to_dict()
                # Include full order details
                session_data['orders'] = [order.to_dict() for order in session.orders]
                return jsonify(session_data), 200
            
            elif request.method == 'PUT':
                data = request.get_json()
                
                if 'name' in data:
                    session.name = data['name']
                if 'status' in data:
                    session.status = data['status']
                    # Track timestamps
                    if data['status'] == 'IN_PROGRESS' and not session.started_at:
                        session.started_at = datetime.now(timezone.utc)
                    elif data['status'] == 'COMPLETED' and not session.completed_at:
                        session.completed_at = datetime.now(timezone.utc)
                if 'notes' in data:
                    session.notes = data['notes']
                if 'order_ids' in data:
                    # Reassign orders
                    # First, clear existing assignments
                    for order in session.orders:
                        order.print_session_id = None
                    # Then assign new orders
                    total_estimated = 0
                    for order_id in data['order_ids']:
                        order = Order.query.filter_by(id=order_id, user_id=current_user.id).first()
                        if order:
                            order.print_session_id = session.id
                            if order.estimated_print_time:
                                total_estimated += order.estimated_print_time
                    session.total_estimated_time = total_estimated
                
                db.session.commit()
                return jsonify(session.to_dict()), 200
            
            elif request.method == 'DELETE':
                # Unassign orders first
                for order in session.orders:
                    order.print_session_id = None
                db.session.delete(session)
                db.session.commit()
                return jsonify({'message': 'Print session deleted'}), 200
        
        except Exception as e:
            db.session.rollback()
            print(f"Error managing print session: {e}")
            return jsonify({'error': 'Failed to manage print session'}), 500
    
    # ==================== ADVANCED FEATURES ====================
    
    # File Upload/3D Model Viewer
    @app.route('/api/files', methods=['GET', 'POST'])
    @token_required
    def customer_files():
        """List or upload customer files"""
        try:
            current_user = request.user
            if request.method == 'GET':
                customer_id = request.args.get('customer_id')
                order_id = request.args.get('order_id')
                file_type = request.args.get('file_type')
                
                query = CustomerFile.query.filter_by(user_id=current_user.id)
                if customer_id:
                    query = query.filter_by(customer_id=customer_id)
                if order_id:
                    query = query.filter_by(order_id=order_id)
                if file_type:
                    query = query.filter_by(file_type=file_type)
                
                files = query.order_by(CustomerFile.created_at.desc()).all()
                return jsonify({'files': [f.to_dict() for f in files], 'total': len(files)}), 200
            
            if 'file' not in request.files:
                return jsonify({'error': 'No file provided'}), 400
            
            file = request.files['file']
            if file.filename == '':
                return jsonify({'error': 'Empty filename'}), 400
            
            original_filename = secure_filename(file.filename)
            file_ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            filename = f"{timestamp}_{original_filename}"
            
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)
            
            file_type = 'other'
            if file_ext in ['stl', 'obj', '3mf']:
                file_type = '3d_model'
            elif file_ext in ['gcode', 'gco']:
                file_type = 'gcode'
            elif file_ext in ['jpg', 'jpeg', 'png', 'gif']:
                file_type = 'image'
            elif file_ext == 'pdf':
                file_type = 'pdf'
            
            customer_file = CustomerFile(
                user_id=current_user.id,
                customer_id=request.form.get('customer_id'),
                order_id=request.form.get('order_id'),
                filename=filename,
                original_filename=original_filename,
                file_path=file_path,
                file_type=file_type,
                file_size=os.path.getsize(file_path),
                mime_type=file.content_type,
                description=request.form.get('description')
            )
            db.session.add(customer_file)
            db.session.commit()
            
            return jsonify(customer_file.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f"Error uploading customer file: {e}")
            return jsonify({'error': 'Failed to upload file'}), 500
    
    @app.route('/api/files/<int:file_id>', methods=['GET', 'DELETE'])
    @token_required
    def customer_file_detail(file_id):
        """Get or delete a specific file"""
        try:
            current_user = request.user
            file = CustomerFile.query.filter_by(id=file_id, user_id=current_user.id).first()
            if not file:
                return jsonify({'error': 'File not found'}), 404
            
            if request.method == 'GET':
                safe_name = secure_filename(file.filename)
                if not safe_name or safe_name != file.filename:
                    return jsonify({'error': 'Invalid file reference'}), 400
                return send_from_directory(app.config['UPLOAD_FOLDER'], safe_name, as_attachment=True, download_name=file.original_filename)
            
            if request.method == 'DELETE':
                if os.path.exists(file.file_path):
                    os.remove(file.file_path)
                db.session.delete(file)
                db.session.commit()
                return jsonify({'message': 'File deleted'}), 200
        except Exception as e:
            db.session.rollback()
            print(f"Error managing file: {e}")
            return jsonify({'error': 'Failed to manage file'}), 500
    
    # Etsy Message Parsing
    @app.route('/api/etsy/messages', methods=['GET'])
    @token_required
    def get_etsy_messages():
        """Fetch and parse Etsy messages for custom requests"""
        try:
            current_user = request.user
            
            # Refresh token if needed
            if current_user.token_expires_at and current_user.token_expires_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
                token_data = EtsyOAuth.refresh_access_token(current_user.refresh_token)
                current_user.access_token = token_data['access_token']
                current_user.refresh_token = token_data.get('refresh_token', current_user.refresh_token)
                current_user.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data.get('expires_in', 3600))
                db.session.commit()
            
            if not current_user.shop_id:
                return jsonify({'error': 'No shop associated with account'}), 404
            
            # Fetch recent conversations (Etsy API v3: /shops/{shop_id}/conversations)
            headers = {
                'Authorization': f'Bearer {current_user.access_token}',
                'x-api-key': app.config['ETSY_CLIENT_ID']
            }
            
            try:
                response = requests.get(
                    f'https://api.etsy.com/v3/application/shops/{current_user.shop_id}/conversations',
                    headers=headers,
                    params={'limit': 25},
                    timeout=app.config.get('HTTP_TIMEOUT', 10)
                )
                response.raise_for_status()
                conversations = response.json().get('results', [])
                
                # Parse for custom request keywords
                keywords = ['custom', 'request', 'specific', 'personalize', 'modify', 'change', 'special']
                parsed_messages = []
                
                for conv in conversations:
                    last_message = conv.get('last_message', '')
                    if any(keyword in last_message.lower() for keyword in keywords):
                        buyer_user_id = conv.get('buyer_user_id')
                        
                        # Try to find customer
                        customer = Customer.query.filter_by(user_id=current_user.id).filter(
                            db.or_(
                                Customer.email.ilike(f"%{buyer_user_id}%"),
                                Customer.name.ilike(f"%{conv.get('other_party_name', '')}%")
                            )
                        ).first()
                        
                        parsed_messages.append({
                            'conversation_id': conv.get('conversation_id'),
                            'buyer_name': conv.get('other_party_name'),
                            'buyer_user_id': buyer_user_id,
                            'last_message': last_message,
                            'customer_id': customer.id if customer else None,
                            'detected_keywords': [kw for kw in keywords if kw in last_message.lower()]
                        })
                
                return jsonify({'messages': parsed_messages, 'total': len(parsed_messages)}), 200
            except Exception as e:
                print(f"Error fetching Etsy messages: {e}")
                return jsonify({'error': 'Failed to fetch messages'}), 500
        except Exception as e:
            print(f"Error in get_etsy_messages: {e}")
            return jsonify({'error': 'Failed to retrieve messages'}), 500
    
    @app.route('/api/etsy/messages/<conversation_id>/create-request', methods=['POST'])
    @token_required
    def create_request_from_message(conversation_id):
        """Create a CustomerRequest from an Etsy message"""
        try:
            current_user = request.user
            data = request.get_json() or {}
            
            customer_id = data.get('customer_id')
            if not customer_id:
                return jsonify({'error': 'customer_id is required'}), 400
            
            customer = Customer.query.filter_by(id=customer_id, user_id=current_user.id).first()
            if not customer:
                return jsonify({'error': 'Customer not found'}), 404
            
            req = CustomerRequest(
                user_id=current_user.id,
                customer_id=customer_id,
                title=data.get('title', f'Custom request from conversation {conversation_id}'),
                description=data.get('description', ''),
                status='open',
                priority=data.get('priority', 'normal')
            )
            db.session.add(req)
            db.session.commit()
            
            return jsonify(req.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f"Error creating customer request: {e}")
            return jsonify({'error': 'Failed to create request'}), 500
    
    # Printer Connection & Monitoring
    @app.route('/api/printer-connections', methods=['GET', 'POST'])
    @token_required
    def printer_connections():
        """List or create printer API connections"""
        try:
            current_user = request.user
            if request.method == 'GET':
                connections = PrinterConnection.query.filter_by(user_id=current_user.id).all()
                return jsonify({'connections': [c.to_dict() for c in connections], 'total': len(connections)}), 200
            
            data = request.get_json() or {}
            printer_id = data.get('printer_id')
            if not printer_id:
                return jsonify({'error': 'printer_id is required'}), 400
            
            printer = Printer.query.filter_by(id=printer_id, user_id=current_user.id).first()
            if not printer:
                return jsonify({'error': 'Printer not found'}), 404
            
            connection = PrinterConnection(
                printer_id=printer_id,
                user_id=current_user.id,
                connection_type=data.get('connection_type', 'octoprint'),
                api_url=data['api_url'],
                api_key=data.get('api_key'),
                serial_number=data.get('serial_number'),
                access_code=data.get('access_code'),
                webhook_enabled=data.get('webhook_enabled', False)
            )
            db.session.add(connection)
            db.session.commit()
            
            return jsonify(connection.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/printer-connections/<int:connection_id>/status', methods=['GET'])
    @token_required
    def get_printer_status(connection_id):
        """Get current printer status from OctoPrint/Klipper"""
        try:
            current_user = request.user
            connection = PrinterConnection.query.filter_by(id=connection_id, user_id=current_user.id).first()
            if not connection:
                return jsonify({'error': 'Connection not found'}), 404
            
            headers = {}
            if connection.api_key:
                if connection.connection_type == 'octoprint':
                    headers['X-Api-Key'] = connection.api_key
                elif connection.connection_type in ['klipper', 'moonraker']:
                    headers['Authorization'] = f'Bearer {connection.api_key}'
            
            try:
                if connection.connection_type == 'octoprint':
                    response = requests.get(f"{connection.api_url}/api/printer", headers=headers, timeout=5)
                elif connection.connection_type in ['klipper', 'moonraker']:
                    response = requests.get(f"{connection.api_url}/printer/info", headers=headers, timeout=5)
                elif connection.connection_type == 'bambu_cloud':
                    # Bambu Cloud API - requires authentication token
                    if not connection.api_key:
                        return jsonify({'error': 'API key required for Bambu Cloud'}), 400
                    headers['Authorization'] = f'Bearer {connection.api_key}'
                    response = requests.get(
                        f"https://api.bambulab.com/v1/iot-service/api/user/device/{connection.serial_number}",
                        headers=headers,
                        timeout=5
                    )
                elif connection.connection_type == 'bambu_lan':
                    # Bambu LAN mode - MQTT-based, use simplified HTTP polling to device IP
                    # Format: http://{printer_ip}/api/status
                    response = requests.get(
                        f"{connection.api_url}/api/status",
                        auth=('bblp', connection.access_code) if connection.access_code else None,
                        timeout=5
                    )
                else:
                    return jsonify({'error': 'Unsupported connection type'}), 400
                
                response.raise_for_status()
                status_data = response.json()
                
                # Parse Bambu Lab status into standardized format
                if connection.connection_type in ['bambu_cloud', 'bambu_lan']:
                    # Extract relevant fields from Bambu response
                    parsed_status = {
                        'state': status_data.get('print', {}).get('gcode_state', 'UNKNOWN'),
                        'progress': status_data.get('print', {}).get('mc_percent', 0),
                        'current_layer': status_data.get('print', {}).get('layer_num', 0),
                        'total_layers': status_data.get('print', {}).get('total_layer_num', 0),
                        'bed_temp': status_data.get('print', {}).get('bed_temper', 0),
                        'nozzle_temp': status_data.get('print', {}).get('nozzle_temper', 0),
                        'chamber_temp': status_data.get('print', {}).get('chamber_temper', 0),
                        'print_error': status_data.get('print', {}).get('print_error', 0),
                        'raw': status_data
                    }
                    status_data = parsed_status
                
                connection.status = 'connected'
                connection.last_connected_at = datetime.now(timezone.utc)
                db.session.commit()
                
                return jsonify({'status': status_data, 'connection_status': 'connected'}), 200
            except Exception as e:
                connection.status = 'error'
                db.session.commit()
                print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    # Weather & Filament Recommendations
    @app.route('/api/weather/filament-recommendations', methods=['GET'])
    @token_required
    def filament_recommendations():
        """Get weather-based filament handling recommendations"""
        try:
            location = request.args.get('location', 'auto')
            
            # Use a weather API (e.g., OpenWeatherMap)
            api_key = os.getenv('OPENWEATHER_API_KEY')
            if not api_key:
                return jsonify({
                    'recommendations': {
                        'humidity': None,
                        'tips': ['Configure OPENWEATHER_API_KEY to get real-time humidity data']
                    }
                }), 200
            
            try:
                if location == 'auto':
                    # Get location from IP (simplified)
                    location = 'New York,US'
                
                weather_url = f'http://api.openweathermap.org/data/2.5/weather?q={location}&appid={api_key}'
                response = requests.get(weather_url, timeout=5)
                response.raise_for_status()
                weather_data = response.json()
                
                humidity = weather_data.get('main', {}).get('humidity')
                temp = weather_data.get('main', {}).get('temp', 0) - 273.15  # Kelvin to Celsius
                
                tips = []
                if humidity and humidity > 60:
                    tips.append('High humidity detected! Store PLA in airtight containers with desiccant.')
                    tips.append('Consider pre-drying filament before printing.')
                    tips.append('Nylon and TPU are especially hygroscopic - use dry boxes.')
                elif humidity and humidity < 30:
                    tips.append('Low humidity - ideal printing conditions!')
                    tips.append('Still recommended to store filament sealed when not in use.')
                
                if temp and temp < 15:
                    tips.append('Cold temperature - consider enclosing printer for ABS/ASA.')
                elif temp and temp > 30:
                    tips.append('Warm temperature - ensure adequate cooling for PLA.')
                
                return jsonify({
                    'location': weather_data.get('name'),
                    'humidity': humidity,
                    'temperature_c': round(temp, 1) if temp else None,
                    'tips': tips
                }), 200
            except Exception as e:
                print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    # ==================== BAMBU CONNECT - MATERIALS ====================
    @app.route('/api/bambu/materials/<int:printer_id>', methods=['GET'])
    @token_required
    def get_printer_materials(user_id, printer_id):
        """Get materials loaded on Bambu printer"""
        try:
            printer = Printer.query.get_or_404(printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            materials = BambuMaterial.query.filter_by(printer_id=printer_id).all()
            return jsonify([m.to_dict() for m in materials]), 200
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/bambu/materials/<int:printer_id>', methods=['POST'])
    @token_required
    def add_printer_material(user_id, printer_id):
        """Add material to Bambu printer slot"""
        try:
            printer = Printer.query.get_or_404(printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            data = request.json
            material = BambuMaterial(
                user_id=user_id,
                printer_id=printer_id,
                slot=data.get('slot'),
                material_type=data.get('material_type'),
                color=data.get('color'),
                weight_grams=data.get('weight_grams'),
                remaining_pct=data.get('remaining_pct', 100),
                vendor=data.get('vendor'),
                cost_per_kg=data.get('cost_per_kg')
            )
            db.session.add(material)
            db.session.commit()
            return jsonify(material.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/bambu/materials/<int:material_id>', methods=['PUT'])
    @token_required
    def update_printer_material(user_id, material_id):
        """Update material remaining percentage"""
        try:
            material = BambuMaterial.query.get_or_404(material_id)
            printer = Printer.query.get(material.printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            data = request.json
            if 'remaining_pct' in data:
                material.remaining_pct = data['remaining_pct']
            if 'material_type' in data:
                material.material_type = data['material_type']
            if 'color' in data:
                material.color = data['color']
            if 'weight_grams' in data:
                material.weight_grams = data['weight_grams']
            
            material.last_synced = datetime.utcnow()
            material.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify(material.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    # ==================== BAMBU CONNECT - NOTIFICATIONS ====================
    @app.route('/api/bambu/notifications/<int:printer_id>', methods=['GET'])
    @token_required
    def get_printer_notifications(user_id, printer_id):
        """Get notification preferences for printer"""
        try:
            printer = Printer.query.get_or_404(printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            notif = PrintNotification.query.filter_by(printer_id=printer_id).first()
            if not notif:
                # Create default notifications
                notif = PrintNotification(
                    user_id=user_id,
                    printer_id=printer_id,
                    notify_print_start=True,
                    notify_print_complete=True,
                    notify_print_failed=True,
                    email_enabled=True
                )
                db.session.add(notif)
                db.session.commit()
            
            return jsonify(notif.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/bambu/notifications/<int:printer_id>', methods=['PUT'])
    @token_required
    def update_printer_notifications(user_id, printer_id):
        """Update notification preferences"""
        try:
            printer = Printer.query.get_or_404(printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            notif = PrintNotification.query.filter_by(printer_id=printer_id).first()
            if not notif:
                notif = PrintNotification(user_id=user_id, printer_id=printer_id)
                db.session.add(notif)
            
            data = request.json
            if 'notify_print_start' in data:
                notif.notify_print_start = data['notify_print_start']
            if 'notify_print_complete' in data:
                notif.notify_print_complete = data['notify_print_complete']
            if 'notify_print_failed' in data:
                notif.notify_print_failed = data['notify_print_failed']
            if 'notify_material_change' in data:
                notif.notify_material_change = data['notify_material_change']
            if 'notify_maintenance' in data:
                notif.notify_maintenance = data['notify_maintenance']
            if 'email_enabled' in data:
                notif.email_enabled = data['email_enabled']
            if 'webhook_url' in data:
                notif.webhook_url = data['webhook_url']
            
            notif.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify(notif.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    # ==================== BAMBU CONNECT - PRINT SCHEDULING ====================
    @app.route('/api/bambu/scheduled-prints/<int:printer_id>', methods=['GET'])
    @token_required
    def get_scheduled_prints(user_id, printer_id):
        """Get scheduled print jobs for printer"""
        try:
            printer = Printer.query.get_or_404(printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            status = request.args.get('status')
            query = ScheduledPrint.query.filter_by(printer_id=printer_id)
            
            if status:
                query = query.filter_by(status=status)
            
            # Order by scheduled_start for queued jobs, then by priority
            prints = query.order_by(
                ScheduledPrint.status,
                ScheduledPrint.scheduled_start.asc(),
                ScheduledPrint.priority.desc()
            ).all()
            
            return jsonify([p.to_dict() for p in prints]), 200
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/bambu/scheduled-prints', methods=['POST'])
    @token_required
    def create_scheduled_print(user_id):
        """Create a scheduled print job"""
        try:
            data = request.json
            printer_id = data.get('printer_id')
            
            printer = Printer.query.get_or_404(printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            scheduled_print = ScheduledPrint(
                user_id=user_id,
                printer_id=printer_id,
                order_id=data.get('order_id'),
                job_name=data.get('job_name', 'Unnamed Print'),
                file_name=data.get('file_name'),
                status=data.get('status', 'queued'),
                scheduled_start=datetime.fromisoformat(data['scheduled_start']) if data.get('scheduled_start') else None,
                estimated_duration_minutes=data.get('estimated_duration_minutes'),
                material_type=data.get('material_type'),
                material_slot=data.get('material_slot'),
                nozzle_temp=data.get('nozzle_temp'),
                bed_temp=data.get('bed_temp'),
                print_speed=data.get('print_speed'),
                priority=data.get('priority', 0),
                notes=data.get('notes')
            )
            db.session.add(scheduled_print)
            db.session.commit()
            return jsonify(scheduled_print.to_dict()), 201
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'An error occurred'}), 500
    
    @app.route('/api/bambu/scheduled-prints/<int:print_id>', methods=['PUT'])
    @token_required
    def update_scheduled_print(user_id, print_id):
        """Update scheduled print job"""
        try:
            scheduled_print = ScheduledPrint.query.get_or_404(print_id)
            printer = Printer.query.get(scheduled_print.printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            data = request.json
            if 'status' in data:
                scheduled_print.status = data['status']
            if 'scheduled_start' in data:
                scheduled_print.scheduled_start = datetime.fromisoformat(data['scheduled_start'])
            if 'priority' in data:
                scheduled_print.priority = data['priority']
            if 'notes' in data:
                scheduled_print.notes = data['notes']
            
            # Update actual execution times
            if data.get('status') == 'started' and not scheduled_print.started_at:
                scheduled_print.started_at = datetime.utcnow()
            elif data.get('status') == 'completed' and not scheduled_print.completed_at:
                scheduled_print.completed_at = datetime.utcnow()
            elif data.get('status') == 'failed' and data.get('failed_reason'):
                scheduled_print.failed_reason = data['failed_reason']
                scheduled_print.completed_at = datetime.utcnow()
            
            scheduled_print.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify(scheduled_print.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f"Error updating scheduled print: {e}")
            return jsonify({'error': 'Failed to update scheduled print'}), 500
    
    @app.route('/api/bambu/scheduled-prints/<int:print_id>', methods=['DELETE'])
    @token_required
    def delete_scheduled_print(user_id, print_id):
        """Cancel/delete scheduled print job"""
        try:
            scheduled_print = ScheduledPrint.query.get_or_404(print_id)
            printer = Printer.query.get(scheduled_print.printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            db.session.delete(scheduled_print)
            db.session.commit()
            return jsonify({'message': 'Print job deleted'}), 200
        except Exception as e:
            db.session.rollback()
            print(f"Error deleting scheduled print: {e}")
            return jsonify({'error': 'Failed to delete print job'}), 500
    
    @app.route('/api/bambu/scheduled-prints/<int:printer_id>/queue', methods=['GET'])
    @token_required
    def get_print_queue(user_id, printer_id):
        """Get current print queue (queued and scheduled statuses)"""
        try:
            printer = Printer.query.get_or_404(printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            queue = ScheduledPrint.query.filter(
                ScheduledPrint.printer_id == printer_id,
                ScheduledPrint.status.in_(['queued', 'scheduled'])
            ).order_by(
                ScheduledPrint.priority.desc(),
                ScheduledPrint.scheduled_start.asc()
            ).all()
            
            return jsonify([p.to_dict() for p in queue]), 200
        except Exception as e:
            print(f"Error getting print queue: {e}")
            return jsonify({'error': 'Failed to get print queue'}), 500
    
    @app.route('/api/orders/<int:order_id>/schedule-prints', methods=['POST'])
    @token_required
    def schedule_order_for_print(user_id, order_id):
        """Schedule all items in an order for printing"""
        try:
            order = Order.query.get_or_404(order_id)
            if order.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            data = request.json
            printer_id = data.get('printer_id')
            material_type = data.get('material_type')
            start_offset_minutes = data.get('start_offset_minutes', 0)
            
            if not printer_id:
                return jsonify({'error': 'printer_id required'}), 400
            
            # Verify printer exists and belongs to user
            printer = Printer.query.get_or_404(printer_id)
            if printer.user_id != user_id:
                return jsonify({'error': 'Unauthorized'}), 403
            
            # Schedule prints
            scheduled = schedule_order_prints(
                user_id=user_id,
                order_id=order_id,
                printer_id=printer_id,
                material_type=material_type,
                start_offset_minutes=start_offset_minutes
            )
            
            return jsonify({
                'message': f'Scheduled {len(scheduled)} print jobs',
                'prints': [p.to_dict() for p in scheduled]
            }), 201
        except ValueError as e:
            print(f"Validation error scheduling prints: {e}")
            return jsonify({'error': 'Invalid scheduling parameters'}), 400
        except Exception as e:
            db.session.rollback()
            print(f"Error scheduling prints: {e}")
            return jsonify({'error': 'Failed to schedule prints'}), 500
    
    # ==================== HEALTH CHECK ====================
    @app.route('/api/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        return jsonify({'status': 'healthy'}), 200
    
    # Error handlers
    @app.route('/uploads/<path:filename>')
    def serve_upload(filename):
        safe_name = secure_filename(filename)
        if not safe_name:
            abort(404)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], safe_name)
        if not os.path.exists(file_path):
            abort(404)
        return send_from_directory(app.config['UPLOAD_FOLDER'], safe_name)

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500
    
    # ==================== ALERTS: SETTINGS, PREVIEW, TRIGGER ====================
    @app.route('/api/alerts/settings', methods=['GET', 'PUT'])
    @token_required
    def alert_settings():
        """Get or update alert destinations (Slack/Discord/email)."""
        try:
            current_user = request.user
            settings = AlertSettings.query.filter_by(user_id=current_user.id).first()
            if request.method == 'GET':
                if not settings:
                    settings = AlertSettings(user_id=current_user.id)
                    db.session.add(settings)
                    db.session.commit()
                return jsonify(settings.to_dict()), 200

            # PUT
            data = request.get_json() or {}
            if not settings:
                settings = AlertSettings(user_id=current_user.id)
                db.session.add(settings)
            for field in ['slack_webhook_url', 'discord_webhook_url', 'email_enabled', 'email_to']:
                if field in data:
                    setattr(settings, field, data[field])
            settings.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify(settings.to_dict()), 200
        except Exception as e:
            db.session.rollback()
            print(f'Exception: {e}'); return jsonify({'error': 'Failed to update alert settings'}), 500

    @app.route('/api/alerts/preview', methods=['GET'])
    @token_required
    def alert_preview():
        """Return current low-stock filaments and printer issues for the user."""
        try:
            current_user = request.user
            filaments = Filament.query.filter_by(user_id=current_user.id).all()
            low_stock = [f.to_dict() for f in filaments if (f.current_amount or 0) <= (f.low_stock_threshold or 0)]

            printers = Printer.query.filter_by(user_id=current_user.id).all()
            issues = []
            for p in printers:
                status = (p.status or '').lower()
                if any(x in status for x in ['error', 'fail', 'fault', 'offline', 'disconnected']):
                    issues.append(p.to_dict())

            return jsonify({'low_stock': low_stock, 'printer_issues': issues}), 200
        except Exception as e:
            logger.exception("Exception in preview_alerts")
            return jsonify({'error': 'Failed to preview alerts'}), 500

    def _send_webhook(url: str | None, text: str) -> bool:
        if not url:
            return False
        
        # Validate URL and detect webhook format
        try:
            parsed = urlparse(url)
            # Ensure URL has proper scheme
            if parsed.scheme not in ('http', 'https'):
                logger.warning(f"Invalid webhook URL scheme: {parsed.scheme}")
                return False
            
            # Validate webhook provider by hostname
            hostname = parsed.hostname
            if not hostname:
                logger.warning("Webhook URL has no hostname")
                return False
            
            payload = {}
            # Slack webhook validation
            if hostname == 'hooks.slack.com' or hostname.endswith('.slack.com'):
                if not parsed.path.startswith('/services/'):
                    logger.warning(f"Invalid Slack webhook path: {parsed.path}")
                    return False
                payload = {'text': text}
            # Discord webhook validation
            elif hostname == 'discord.com' or hostname.endswith('.discord.com'):
                if not parsed.path.startswith('/api/webhooks/'):
                    logger.warning(f"Invalid Discord webhook path: {parsed.path}")
                    return False
                payload = {'content': text}
            else:
                # Generic webhook format for other providers
                payload = {'message': text}
            
            resp = requests.post(url, json=payload, timeout=app.config.get('HTTP_TIMEOUT', 10))
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error(f"Webhook send failed: {type(e).__name__}")
            return False

    def _send_email(to_addr: str | None, subject: str, body: str) -> bool:
        if not to_addr:
            return False
        host = os.getenv('SMTP_HOST')
        port = int(os.getenv('SMTP_PORT', '587'))
        user = os.getenv('SMTP_USER')
        password = os.getenv('SMTP_PASS')
        from_addr = os.getenv('EMAIL_FROM', user or 'alerts@j3d.local')
        try:
            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = from_addr
            msg['To'] = to_addr
            msg.set_content(body)

            with smtplib.SMTP(host, port, timeout=10) as smtp:
                smtp.starttls()
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
            return True
        except Exception as e:
            print(f"Email send failed: {e}")
            return False

    @app.route('/api/alerts/trigger', methods=['POST'])
    @token_required
    def trigger_alerts():
        """Trigger alerts for current low stock and printer issues via configured channels."""
        try:
            current_user = request.user
            settings = AlertSettings.query.filter_by(user_id=current_user.id).first()
            if not settings:
                settings = AlertSettings(user_id=current_user.id)
                db.session.add(settings)
                db.session.commit()

            # Gather data
            filaments = Filament.query.filter_by(user_id=current_user.id).all()
            low_stock_filaments = [f for f in filaments if (f.current_amount or 0) <= (f.low_stock_threshold or 0)]
            printers = Printer.query.filter_by(user_id=current_user.id).all()
            issue_printers = [p for p in printers if any(x in (p.status or '').lower() for x in ['error', 'fail', 'fault', 'offline', 'disconnected'])]

            if not low_stock_filaments and not issue_printers:
                return jsonify({'sent': False, 'message': 'No alerts to send'}), 200

            # Compose message
            lines = [f"Shop: {current_user.username or 'Your shop'}"]
            if low_stock_filaments:
                lines.append("\nLow-stock filaments:")
                for f in low_stock_filaments[:10]:
                    lines.append(f"- {f.material} {f.color}: {f.current_amount}{f.unit} (threshold {f.low_stock_threshold}{f.unit})")
                if len(low_stock_filaments) > 10:
                    lines.append(f"+{len(low_stock_filaments) - 10} more...")
            if issue_printers:
                lines.append("\nPrinter issues:")
                for p in issue_printers[:10]:
                    lines.append(f"- {p.name}: {p.status}")
                if len(issue_printers) > 10:
                    lines.append(f"+{len(issue_printers) - 10} more...")
            message = "\n".join(lines)

            # Dispatch
            sent_channels = []
            if _send_webhook(settings.slack_webhook_url, message):
                sent_channels.append('slack')
            if _send_webhook(settings.discord_webhook_url, message):
                sent_channels.append('discord')
            if settings.email_enabled and _send_email(settings.email_to, 'J3D Alerts', message):
                sent_channels.append('email')

            return jsonify({
                'sent': len(sent_channels) > 0,
                'channels': sent_channels,
                'low_stock_count': len(low_stock_filaments),
                'printer_issue_count': len(issue_printers)
            }), 200
        except Exception as e:
            print(f'Exception: {e}'); return jsonify({'error': 'Failed to trigger alerts'}), 500

    return app

if __name__ == "__main__":
    app = create_app(os.getenv('FLASK_ENV', 'development'))
    # Debug mode controlled by environment configuration, never hardcoded
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(debug=debug_mode, port=5000)