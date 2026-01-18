import requests
from datetime import datetime, timedelta, timezone
from flask import current_app
from models import db, Order, OrderItem

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
            
            print(f"DEBUG: Fetching receipts from {start_date} to {end_date}")
            
            # Fetch ALL receipts
            while True:
                print(f"DEBUG: Fetching receipts with offset {offset}")
                response = etsy_api.get_shop_receipts(
                    shop_id,
                    limit=limit,
                    offset=offset,
                    min_created=min_created,
                    max_created=max_created
                )
                
                receipts = response.get('results', [])
                print(f"DEBUG: Received {len(receipts)} receipts")
                
                if not receipts:
                    break
                
                all_receipts.extend(receipts)
                
                # Check if there are more results
                count = response.get('count', 0)
                if len(all_receipts) >= count:
                    break
                
                offset += limit
            
            print(f"DEBUG: Total receipts fetched: {len(all_receipts)}")
            
            # Count statuses for debugging
            status_counts = {}
            
            # Save to database
            saved_count = 0
            updated_count = 0
            
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
                
                print(f"DEBUG: Receipt {receipt_id} - Etsy status: '{etsy_status}' -> Internal: '{status}'")
                
                if existing_order:
                    # Update existing order
                    existing_order.status = status
                    existing_order.updated_at = datetime.fromtimestamp(receipt_data.get('update_timestamp', 0), tz=timezone.utc)
                    if receipt_data.get('shipped_timestamp'):
                        existing_order.shipped_at = datetime.fromtimestamp(receipt_data['shipped_timestamp'], tz=timezone.utc)
                    updated_count += 1
                else:
                    # Create new order
                    order = Order(
                        user_id=user.id,
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
                        print(f"DEBUG: Error fetching transactions for receipt {receipt_id}: {e}")
                    
                    db.session.add(order)
                    saved_count += 1
            
            db.session.commit()
            
            print(f"DEBUG: Status distribution: {status_counts}")
            
            return {
                'success': True,
                'total_receipts': len(all_receipts),
                'new_orders_saved': saved_count,
                'updated_orders': updated_count,
                'status_counts': status_counts,
                'message': f'Successfully synced {saved_count} new orders and updated {updated_count} existing orders'
            }
        
        except Exception as e:
            print(f"DEBUG: Exception in sync_orders_from_etsy: {str(e)}")
            import traceback
            traceback.print_exc()
            db.session.rollback()
            return {
                'success': False,
                'error': str(e),
                'message': 'Failed to sync orders'
            }