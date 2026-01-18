import requests
from datetime import datetime, timedelta, timezone
from flask import current_app
from models import db, Order, OrderItem, Customer, ScheduledPrint, ProductProfile

class EtsyAPI:
    """Interact with Etsy API v3"""
    
    def __init__(self, access_token):
        self.access_token = access_token
        self.base_url = current_app.config['ETSY_API_BASE_URL']
        self.headers = {
            'Authorization': f'Bearer {access_token}',
            'x-api-key': current_app.config['ETSY_CLIENT_ID']
        }
    
    def _make_request(self, method, endpoint, **kwargs):
        """Make a request to Etsy API"""
        url = f"{self.base_url}{endpoint}"
        kwargs['headers'] = self.headers
        kwargs.setdefault('timeout', current_app.config.get('HTTP_TIMEOUT', 10))
        
        try:
            response = requests.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"Etsy API error: {str(e)}")
    
    def get_shop_receipts(self, shop_id, **params):
        """
        Get shop receipts (orders/transactions)
        
        Parameters:
            shop_id: The shop ID
            limit: Number of results (max 100)
            offset: Offset for pagination
            min_created: Unix timestamp for minimum creation date
            max_created: Unix timestamp for maximum creation date
        """
        return self._make_request('GET', f'/application/shops/{shop_id}/receipts', params=params)
    
    def get_receipt_details(self, shop_id, receipt_id):
        """Get detailed information about a specific receipt"""
        return self._make_request('GET', f'/application/shops/{shop_id}/receipts/{receipt_id}')
    
    def get_receipt_transactions(self, shop_id, receipt_id):
        """Get transactions (line items) for a receipt"""
        return self._make_request('GET', f'/application/shops/{shop_id}/receipts/{receipt_id}/transactions')

class OrderSyncManager:
    """Manage syncing orders from Etsy to local database"""
    
    @staticmethod
    def normalize_status(etsy_status):
        """
        Normalize Etsy status to internal status format
        
        Etsy statuses (from API):
        - open: Created but not paid (legacy)
        - paid: Paid and ready for shipping
        - completed: Shipped and complete
        - payment processing: Payment submitted but not processed
        - canceled: Order canceled
        
        Internal statuses:
        - NEW: Created but not paid
        - PROCESSING: Payment being processed
        - PAID: Paid and ready for shipping
        - COMPLETED: Shipped and complete
        - CANCELED: Order canceled
        """
        status_map = {
            'open': 'NEW',
            'payment processing': 'PROCESSING',
            'paid': 'PAID',
            'completed': 'COMPLETED',
            'canceled': 'CANCELED'
        }
        
        # Return mapped status or uppercase the original if not in map
        return status_map.get(etsy_status.lower(), etsy_status.upper())
    
    @staticmethod
    def sync_orders_from_etsy(user, shop_id, etsy_api, months=6):
        """
        Sync orders from the last N months from Etsy to database
        """
        try:
            # Calculate date range (last N months)
            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=months * 30)
            
            # Convert to Unix timestamps
            min_created = int(start_date.timestamp())
            max_created = int(end_date.timestamp())
            
            all_receipts = []
            offset = 0
            limit = 100
            
            # Fetch ALL receipts
            while True:
                response = etsy_api.get_shop_receipts(
                    shop_id,
                    limit=limit,
                    offset=offset,
                    min_created=min_created,
                    max_created=max_created
                )
                
                receipts = response.get('results', [])
                
                if not receipts:
                    break
                
                all_receipts.extend(receipts)
                
                # Check if there are more results
                count = response.get('count', 0)
                if len(all_receipts) >= count:
                    break
                
                offset += limit
            
            # Count statuses for debugging
            status_counts = {}
            
            # Save to database
            saved_count = 0
            updated_count = 0

            def upsert_customer(receipt_data, add_order=True):
                email = (receipt_data.get('buyer_email') or '').strip().lower()
                name = receipt_data.get('name') or receipt_data.get('first_line') or ''
                if not email and not name:
                    return None

                customer = None
                if email:
                    customer = Customer.query.filter_by(user_id=user.id, email=email).first()
                if not customer and name:
                    customer = Customer.query.filter_by(user_id=user.id, name=name).first()

                order_created_at = datetime.fromtimestamp(receipt_data.get('create_timestamp', 0), tz=timezone.utc)
                order_value = float(receipt_data.get('grandtotal', {}).get('amount', 0)) / 100

                if not customer:
                    customer = Customer(
                        user_id=user.id,
                        email=email or None,
                        name=name or None,
                        first_order_at=order_created_at if add_order else None,
                        last_order_at=order_created_at if add_order else None,
                        order_count=1 if add_order else 0,
                        total_spend=order_value if add_order else 0
                    )
                    db.session.add(customer)
                elif add_order:
                    customer.order_count = (customer.order_count or 0) + 1
                    customer.total_spend = (customer.total_spend or 0) + order_value
                    if not customer.first_order_at or order_created_at < customer.first_order_at:
                        customer.first_order_at = order_created_at
                    if not customer.last_order_at or order_created_at > customer.last_order_at:
                        customer.last_order_at = order_created_at

                return customer
            
            for receipt_data in all_receipts:
                # Check if order already exists
                receipt_id = str(receipt_data['receipt_id'])
                existing_order = Order.query.filter_by(
                    etsy_order_id=receipt_id
                ).first()
                
                # Get status directly from receipt data
                etsy_status = receipt_data.get('status', 'open')
                status = OrderSyncManager.normalize_status(etsy_status)
                
                # Track status counts for debugging
                status_counts[status] = status_counts.get(status, 0) + 1
                
                if existing_order:
                    # Update existing order
                    existing_order.status = status
                    existing_order.updated_at = datetime.fromtimestamp(receipt_data.get('update_timestamp', 0), tz=timezone.utc)
                    if receipt_data.get('shipped_timestamp'):
                        existing_order.shipped_at = datetime.fromtimestamp(receipt_data['shipped_timestamp'], tz=timezone.utc)
                    if not existing_order.customer_id:
                        customer = upsert_customer(receipt_data, add_order=False)
                        if customer:
                            existing_order.customer_id = customer.id
                    updated_count += 1
                else:
                    # Create new order
                    customer = upsert_customer(receipt_data, add_order=True)
                    order = Order(
                        user_id=user.id,
                        customer_id=customer.id if customer else None,
                        etsy_order_id=receipt_id,
                        etsy_shop_id=str(shop_id),
                        buyer_email=receipt_data.get('buyer_email', ''),
                        buyer_name=receipt_data.get('name', ''),
                        total_amount=float(receipt_data.get('grandtotal', {}).get('amount', 0)) / 100,  # Convert cents to dollars
                        currency=receipt_data.get('grandtotal', {}).get('currency_code', 'USD'),
                        status=status,
                        created_at=datetime.fromtimestamp(receipt_data.get('create_timestamp', 0), tz=timezone.utc),
                        updated_at=datetime.fromtimestamp(receipt_data.get('update_timestamp', 0), tz=timezone.utc)
                    )
                    
                    if receipt_data.get('shipped_timestamp'):
                        order.shipped_at = datetime.fromtimestamp(receipt_data['shipped_timestamp'], tz=timezone.utc)
                    
                    # Get transactions (line items) for this receipt
                    try:
                        transactions_response = etsy_api.get_receipt_transactions(shop_id, receipt_id)
                        transactions = transactions_response.get('results', [])
                        
                        for transaction in transactions:
                            item = OrderItem(
                                etsy_listing_id=str(transaction.get('listing_id', '')),
                                title=transaction.get('title', ''),
                                quantity=transaction.get('quantity', 1),
                                price=float(transaction.get('price', {}).get('amount', 0)) / 100  # Convert cents to dollars
                            )
                            order.items.append(item)
                    except Exception as e:
                        pass  # Silently ignore transaction fetch errors
                    
                    db.session.add(order)
                    saved_count += 1
            
            db.session.commit()
            
            return {
                'success': True,
                'total_receipts': len(all_receipts),
                'new_orders_saved': saved_count,
                'updated_orders': updated_count,
                'status_counts': status_counts,
                'message': f'Successfully synced {saved_count} new orders and updated {updated_count} existing orders'
            }
        
        except Exception as e:
            import traceback
            traceback.print_exc()
            db.session.rollback()
            return {
                'success': False,
                'error': str(e),
                'message': 'Failed to sync orders'
            }


def schedule_order_prints(user_id, order_id, printer_id, material_type=None, start_offset_minutes=0):
    """
    Automatically create scheduled print jobs for order items
    
    Args:
        user_id: User ID
        order_id: Order ID to schedule
        printer_id: Target printer
        material_type: Optional material type override
        start_offset_minutes: Delay before first print starts (default 0)
    
    Returns:
        List of created ScheduledPrint objects
    """
    from models import Printer
    
    order = Order.query.get(order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")
    
    printer = Printer.query.get(printer_id)
    if not printer or printer.user_id != user_id:
        raise ValueError(f"Printer {printer_id} not found or unauthorized")
    
    scheduled_prints = []
    current_start_time = datetime.utcnow() + timedelta(minutes=start_offset_minutes)
    
    for idx, item in enumerate(order.items):
        # Try to find product profile for print settings
        product = ProductProfile.query.filter_by(
            user_id=user_id,
            product_name=item.title
        ).first()
        
        scheduled_print = ScheduledPrint(
            user_id=user_id,
            printer_id=printer_id,
            order_id=order_id,
            job_name=f"{order.order_number} - {item.title}",
            file_name=f"{item.title.replace(' ', '_')}.stl",
            status='queued',
            scheduled_start=current_start_time if idx == 0 else None,
            estimated_duration_minutes=product.print_time_minutes if product else 120,
            material_type=material_type or (product.preferred_material if product else 'PLA'),
            nozzle_temp=product.nozzle_temp_c if product else 200,
            bed_temp=product.bed_temp_c if product else 60,
            print_speed=product.print_speed_mms if product else 50,
            priority=10 - idx,  # Higher priority for earlier items
            notes=f"Quantity: {item.quantity}"
        )
        db.session.add(scheduled_print)
        scheduled_prints.append(scheduled_print)
        
        # Offset subsequent prints by estimated duration + buffer
        current_start_time += timedelta(
            minutes=(product.print_time_minutes if product else 120) + 15
        )
    
    db.session.commit()
    return scheduled_prints