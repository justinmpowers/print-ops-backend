"""Order sync with a returning customer: stored order dates come back from the database without
a timezone, and comparing them with Etsy's timezone-aware timestamps used to abort the sync."""
from datetime import datetime, timezone

from models import db, Customer, Order
from etsy_api import OrderSyncManager


class FakeEtsyAPI:
    def __init__(self, receipts):
        self.receipts = receipts

    def get_shop_receipts(self, shop_id, **params):
        return {'results': self.receipts if params.get('offset', 0) == 0 else [], 'count': len(self.receipts)}

    def get_receipt_transactions(self, shop_id, receipt_id):
        return {'results': []}


def _receipt(receipt_id, created):
    return {'receipt_id': receipt_id, 'status': 'paid', 'buyer_email': 'repeat@example.com', 'name': 'Repeat Buyer',
            'create_timestamp': int(created.timestamp()), 'update_timestamp': int(created.timestamp()),
            'grandtotal': {'amount': 2500, 'currency_code': 'USD'}}


def test_returning_customer_does_not_break_sync(app, user):
    # Saved by an earlier sync: naive UTC, as the database returns it.
    customer = Customer(user_id=user.id, email='repeat@example.com', name='Repeat Buyer', order_count=1,
                        total_spend=10.0, first_order_at=datetime(2026, 9, 1, 12, 0), last_order_at=datetime(2026, 9, 1, 12, 0))
    db.session.add(customer)
    db.session.commit()
    db.session.expire_all()   # reload from the database, dropping any in-memory timezone

    new_order_time = datetime.now(timezone.utc).replace(microsecond=0)
    result = OrderSyncManager.sync_orders_from_etsy(user, 'shop1', FakeEtsyAPI([_receipt(5001, new_order_time)]))

    assert result['success'], result
    assert result['new_orders_saved'] == 1
    c = db.session.get(Customer, customer.id)
    assert c.order_count == 2
    assert c.first_order_at.replace(tzinfo=None) == datetime(2026, 9, 1, 12, 0)
    assert c.last_order_at.replace(tzinfo=None) == new_order_time.replace(tzinfo=None)
    assert Order.query.filter_by(etsy_order_id='5001').one().customer_id == customer.id
