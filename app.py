import os
from dotenv import load_dotenv
from flask import Flask, jsonify, request, session
from flask_cors import CORS
from config import config
from models import db, User, Filament, FilamentUsage, Order
from authentication import EtsyOAuth, TokenManager, token_required
from etsy_api import EtsyAPI, OrderSyncManager
from datetime import datetime, timedelta, timezone

# Load environment variables
load_dotenv()

def create_app(config_name='development'):
    """Application factory"""
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(config[config_name])
    
    # Configure session
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
    
    # Initialize extensions
    db.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": app.config['CORS_ORIGINS']}})
    
    # Create database tables
    with app.app_context():
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
            return jsonify({'error': str(e)}), 500
    
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
            print(f"DEBUG: Exception occurred: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e)}), 500
    
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
            print("DEBUG: sync_orders called")
            
            # Check if token needs refresh
            user = request.user
            print(f"DEBUG: User from token_required: {user}")
            print(f"DEBUG: User ID: {user.etsy_user_id}")
            print(f"DEBUG: User shop_id: {user.shop_id}")
            
            # Make token_expires_at timezone-aware if it isn't
            if user.token_expires_at:
                if user.token_expires_at.tzinfo is None:
                    # If naive, assume it's UTC
                    token_expires_at = user.token_expires_at.replace(tzinfo=timezone.utc)
                else:
                    token_expires_at = user.token_expires_at
                
                if token_expires_at <= datetime.now(timezone.utc):
                    print("DEBUG: Token expired, refreshing...")
                    # Refresh token
                    token_data = EtsyOAuth.refresh_access_token(user.refresh_token)
                    user.access_token = token_data['access_token']
                    user.refresh_token = token_data.get('refresh_token', user.refresh_token)
                    user.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=token_data.get('expires_in', 3600))
                    db.session.commit()
                    print("DEBUG: Token refreshed successfully")
            
            # Check if user has a shop_id
            if not user.shop_id:
                print("DEBUG: No shop_id found for user")
                return jsonify({'error': 'No shop associated with this account'}), 404
            
            print("DEBUG: Initializing Etsy API")
            # Initialize Etsy API
            etsy_api = EtsyAPI(user.access_token)
            
            shop_id = user.shop_id
            print(f"DEBUG: Using shop_id: {shop_id}")
            
            print("DEBUG: Starting order sync")
            # Sync orders
            result = OrderSyncManager.sync_orders_from_etsy(user, shop_id, etsy_api, months=6)
            print(f"DEBUG: Sync result: {result}")
            
            return jsonify(result), 200 if result['success'] else 500
        
        except Exception as e:
            print(f"DEBUG: Exception in sync_orders: {str(e)}")
            print(f"DEBUG: Exception type: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            return jsonify({'error': str(e), 'success': False}), 500
    
    @app.route('/api/orders', methods=['GET'])
    @token_required
    def get_orders():
        """Get all orders for authenticated user"""
        try:
            user = request.user
            status = request.args.get('status')
            
            query = Order.query.filter_by(user_id=user.id)
            if status:
                query = query.filter_by(status=status)
            
            orders = query.order_by(Order.created_at.desc()).all()
            
            return jsonify({
                'orders': [order.to_dict() for order in orders],
                'total': len(orders)
            }), 200
        
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
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
            return jsonify({'error': str(e)}), 500
    
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
            return jsonify({'error': str(e)}), 500
    
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
            return jsonify({'error': str(e)}), 500
    
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
            
            filament.updated_at = datetime.utcnow()
            db.session.commit()
            
            return jsonify(filament.to_dict()), 200
        
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 500
    
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
            return jsonify({'error': str(e)}), 500
    
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
            return jsonify({'error': str(e)}), 500
    
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
            return jsonify({'error': str(e)}), 500
    
    # ==================== HEALTH CHECK ====================
    @app.route('/api/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        return jsonify({'status': 'healthy'}), 200
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'error': 'Not found'}), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'error': 'Internal server error'}), 500
    
    return app

if __name__ == "__main__":
    app = create_app(os.getenv('FLASK_ENV', 'development'))
    app.run(debug=True, port=5000)