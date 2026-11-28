import html
import requests
from datetime import datetime, timedelta, timezone
from flask import current_app
from models import utcnow, db, Order, OrderItem, Customer, ScheduledPrint, ProductProfile
import logging

logger = logging.getLogger(__name__)


def _as_utc(dt):
    """A datetime as timezone-aware UTC; naive values from the database are UTC already."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)


def _unescape(value):
    """Etsy returns titles/descriptions with HTML entities escaped (e.g.
    "St Patrick&#39;s Day"). Decode them for display/storage."""
    return html.unescape(value) if value else value


class EtsyAPIError(Exception):
    """Etsy API request failure. Carries the HTTP status code when available
    so callers can distinguish e.g. missing-OAuth-scope (403) from other errors."""
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class EtsyAPI:
    """Interact with Etsy API v3"""

    def __init__(self, access_token):
        self.access_token = access_token
        self.base_url = current_app.config['ETSY_API_BASE_URL']
        self.headers = {
            'Authorization': f'Bearer {access_token}',
            'x-api-key': f"{current_app.config['ETSY_CLIENT_ID']}:{current_app.config['ETSY_CLIENT_SECRET']}"
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
            status_code = getattr(e.response, 'status_code', None)
            raise EtsyAPIError(f"Etsy API error: {str(e)}", status_code=status_code)
    
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

    def get_shop_listings(self, shop_id, **params):
        """
        Get shop listings (products)

        Parameters:
            shop_id: The shop ID
            state: active | inactive | draft | expired | sold_out (default active)
            limit: Number of results (max 100)
            offset: Offset for pagination
            includes: comma-separated list, e.g. "Images"
        """
        params.setdefault('state', 'active')
        return self._make_request('GET', f'/application/shops/{shop_id}/listings', params=params)

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
    def mark_shipped_if_etsy_completed(order, previous_status):
        """Put an order in production status SHIPPED once Etsy reports it completed (shipped).

        Applies when Etsy's status turns COMPLETED, including orders first synced already completed,
        and to orders completed on Etsy before this rule existed that still sit in QUEUED without
        ever being started here. An order that was started in PrintOps and later moved back by hand
        (a reprint, say) is left where it was put.
        """
        if order.status != 'COMPLETED' or order.production_status == 'SHIPPED':
            return
        just_completed = previous_status != 'COMPLETED'
        never_started = order.production_status in (None, 'QUEUED') and order.print_started_at is None
        if just_completed or never_started:
            order.production_status = 'SHIPPED'

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
                    # Stored values come back from the database without a timezone (they're UTC);
                    # compare them as UTC, or a returning customer aborts the whole sync.
                    first, last = _as_utc(customer.first_order_at), _as_utc(customer.last_order_at)
                    if not first or order_created_at < first:
                        customer.first_order_at = order_created_at
                    if not last or order_created_at > last:
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
                    previous_status = existing_order.status
                    existing_order.status = status
                    existing_order.updated_at = datetime.fromtimestamp(receipt_data.get('update_timestamp', 0), tz=timezone.utc)
                    if receipt_data.get('shipped_timestamp'):
                        existing_order.shipped_at = datetime.fromtimestamp(receipt_data['shipped_timestamp'], tz=timezone.utc)
                    OrderSyncManager.mark_shipped_if_etsy_completed(existing_order, previous_status)
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
                    OrderSyncManager.mark_shipped_if_etsy_completed(order, previous_status=None)

                    # Get transactions (line items) for this receipt
                    try:
                        transactions_response = etsy_api.get_receipt_transactions(shop_id, receipt_id)
                        transactions = transactions_response.get('results', [])
                        
                        for transaction in transactions:
                            item = OrderItem(
                                etsy_listing_id=str(transaction.get('listing_id', '')),
                                title=_unescape(transaction.get('title', '')),
                                quantity=transaction.get('quantity', 1),
                                price=float(transaction.get('price', {}).get('amount', 0)) / 100  # Convert cents to dollars
                            )
                            order.items.append(item)
                    except Exception as e:
                        # The order is still saved without its line items; note why.
                        logger.warning("Could not fetch transactions for receipt %s: %s",
                                       receipt_id, type(e).__name__)
                    
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
        
        except Exception:
            # Log full exception details (including stack trace) on the server
            logger.exception("Failed to sync orders from Etsy")
            # Return a generic error message without exposing internal details
            db.session.rollback()
            return {
                'success': False,
                'error': 'An error occurred while syncing orders from Etsy',
                'message': 'Failed to sync orders'
            }


class ListingSyncManager:
    """Manage syncing shop listings (products) from Etsy into local ProductProfile rows"""

    @staticmethod
    def sync_listings_from_etsy(user, shop_id, etsy_api, state='active'):
        """
        Fetch all listings in the given state from Etsy and upsert them into
        ProductProfile, keyed by (user_id, etsy_listing_id).

        Title and description only seed the product once, at creation — both
        are editable via the product's own Edit form, so re-syncing must not
        clobber a manual rename/rewrite. Price/thumbnail/quantity/state/url
        aren't user-editable and are refreshed on every sync. User-authored
        print-setting fields (filament amount, temps, costs, ...) are always
        left untouched on existing rows.
        """
        try:
            all_listings = []
            offset = 0
            limit = 100

            while True:
                response = etsy_api.get_shop_listings(
                    shop_id,
                    state=state,
                    limit=limit,
                    offset=offset,
                    includes='Images'
                )

                listings = response.get('results', [])
                if not listings:
                    break

                all_listings.extend(listings)

                count = response.get('count', 0)
                if len(all_listings) >= count:
                    break

                offset += limit

            created_count = 0
            updated_count = 0
            now = utcnow()

            for listing in all_listings:
                listing_id = str(listing['listing_id'])
                price_data = listing.get('price') or {}
                divisor = price_data.get('divisor', 100) or 100
                price = float(price_data.get('amount', 0)) / divisor

                images = listing.get('images') or []
                thumbnail_url = images[0].get('url_170x135') if images else None

                profile = ProductProfile.query.filter_by(
                    user_id=user.id,
                    etsy_listing_id=listing_id
                ).first()

                if profile:
                    profile.etsy_url = listing.get('url')
                    profile.etsy_thumbnail_url = thumbnail_url
                    profile.etsy_price = price
                    profile.etsy_quantity = listing.get('quantity')
                    profile.etsy_state = listing.get('state')
                    profile.etsy_last_synced_at = now
                    updated_count += 1
                else:
                    profile = ProductProfile(
                        user_id=user.id,
                        product_name=_unescape(listing.get('title')) or f'Etsy listing {listing_id}',
                        description=_unescape(listing.get('description')),
                        standard_filament_amount=0,
                        etsy_listing_id=listing_id,
                        etsy_url=listing.get('url'),
                        etsy_thumbnail_url=thumbnail_url,
                        etsy_price=price,
                        etsy_quantity=listing.get('quantity'),
                        etsy_state=listing.get('state'),
                        etsy_last_synced_at=now
                    )
                    db.session.add(profile)
                    created_count += 1

            db.session.commit()

            return {
                'success': True,
                'total_listings': len(all_listings),
                'new_products_created': created_count,
                'updated_products': updated_count,
                'message': f'Successfully synced {created_count} new products and updated {updated_count} existing products'
            }

        except EtsyAPIError as e:
            logger.exception("Failed to sync listings from Etsy")
            db.session.rollback()
            if e.status_code == 403:
                return {
                    'success': False,
                    'error': 'Etsy denied access to your shop listings. Reconnect your Etsy account to grant the new listings permission.',
                    'needs_reconnect': True,
                    'message': 'Etsy listings permission missing — please reconnect Etsy'
                }
            return {
                'success': False,
                'error': 'An error occurred while syncing listings from Etsy',
                'message': 'Failed to sync products'
            }
        except Exception:
            logger.exception("Failed to sync listings from Etsy")
            db.session.rollback()
            return {
                'success': False,
                'error': 'An error occurred while syncing listings from Etsy',
                'message': 'Failed to sync products'
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
    
    order = db.session.get(Order, order_id)
    if not order:
        raise ValueError(f"Order {order_id} not found")
    
    printer = db.session.get(Printer, printer_id)
    if not printer or printer.user_id != user_id:
        raise ValueError(f"Printer {printer_id} not found or unauthorized")
    
    scheduled_prints = []
    current_start_time = utcnow() + timedelta(minutes=start_offset_minutes)
    
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