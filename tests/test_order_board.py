"""The orders board: dragging a card between columns and within one, and orders Etsy has already
shipped landing in the Shipped column."""
from datetime import datetime, timedelta, timezone

import pytest

from etsy_api import OrderSyncManager
from models import utcnow, db, Order, User

MOVE = '/api/orders/board/move'


@pytest.fixture
def auth(token):
    return {'Authorization': f'Bearer {token}'}


def _order(user, n, status='PAID', production_status='QUEUED', **kwargs):
    order = Order(user_id=user.id, etsy_order_id=f'{user.id}-{n}', etsy_shop_id='shop', buyer_name='Buyer',
                  total_amount=10.0, currency='USD', status=status, production_status=production_status,
                  created_at=utcnow() - timedelta(days=n), **kwargs)
    db.session.add(order)
    db.session.commit()
    return order


def test_move_changes_column_and_saves_card_order(client, user, auth):
    a, b, c = (_order(user, n) for n in (1, 2, 3))
    already_printing = _order(user, 4, production_status='PRINTING')

    resp = client.post(MOVE, headers=auth, json={
        'order_id': a.id, 'production_status': 'PRINTING', 'column_order': [already_printing.id, a.id]})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()['production_status'] == 'PRINTING'

    moved = db.session.get(Order, a.id)
    assert moved.production_status == 'PRINTING'
    assert moved.print_started_at is not None           # same bookkeeping as the Production tab
    assert moved.board_position == 1.0
    assert db.session.get(Order, already_printing.id).board_position == 0.0

    # Reorder within a column: no status change, only positions.
    resp = client.post(MOVE, headers=auth, json={
        'order_id': c.id, 'production_status': 'QUEUED', 'column_order': [c.id, b.id]})
    assert resp.status_code == 200
    assert db.session.get(Order, c.id).board_position == 0.0
    assert db.session.get(Order, b.id).board_position == 1.0
    assert db.session.get(Order, c.id).print_started_at is None


def test_moving_to_failed_counts_one_failure_and_shipped_records_when(client, user, auth):
    order = _order(user, 1, production_status='PRINTING')
    for _ in range(2):   # dropping it in the same column again must not count a second failure
        client.post(MOVE, headers=auth, json={'order_id': order.id, 'production_status': 'FAILED',
                                              'column_order': [order.id]})
    assert db.session.get(Order, order.id).print_failures_count == 1

    client.post(MOVE, headers=auth, json={'order_id': order.id, 'production_status': 'SHIPPED',
                                          'column_order': [order.id]})
    assert db.session.get(Order, order.id).shipped_at is not None


def test_saving_notes_on_a_failed_order_does_not_count_another_failure(client, user, auth):
    order = _order(user, 1, production_status='FAILED', print_failures_count=1)
    resp = client.put(f'/api/orders/{order.id}/production-status', headers=auth,
                      json={'production_status': 'FAILED', 'print_notes': 'nozzle clog'})
    assert resp.status_code == 200
    assert db.session.get(Order, order.id).print_failures_count == 1


SELF = object()   # stands for the moved order's id, which isn't known until the test runs


@pytest.mark.parametrize('body', [
    {'production_status': 'NOPE', 'column_order': [SELF]},
    {'production_status': 'QUEUED', 'column_order': 'not a list'},
    {'production_status': 'QUEUED', 'column_order': [SELF, SELF]},
    {'production_status': 'QUEUED', 'column_order': ['1']},
    {'production_status': 'QUEUED', 'column_order': [True]},
    {'production_status': 'QUEUED', 'column_order': []},           # must include the moved order
    {'production_status': 'QUEUED', 'column_order': list(range(2000))},
])
def test_move_rejects_bad_input(client, user, auth, body):
    order = _order(user, 1)
    column = body['column_order']
    if isinstance(column, list):
        column = [order.id if i is SELF else i for i in column]
    body = dict(body, order_id=order.id, column_order=column)
    assert client.post(MOVE, headers=auth, json=body).status_code == 400


def test_cannot_move_or_reposition_someone_elses_orders(client, user, auth):
    other = User(etsy_user_id='999', username='other', access_token='tok', shop_id='shop2')
    db.session.add(other)
    db.session.commit()
    theirs = _order(other, 1)
    mine = _order(user, 2)

    resp = client.post(MOVE, headers=auth, json={'order_id': theirs.id, 'production_status': 'PRINTING',
                                                 'column_order': [theirs.id]})
    assert resp.status_code == 404
    assert db.session.get(Order, theirs.id).production_status == 'QUEUED'

    # Their id slipped into my column list is ignored.
    resp = client.post(MOVE, headers=auth, json={'order_id': mine.id, 'production_status': 'QUEUED',
                                                 'column_order': [theirs.id, mine.id]})
    assert resp.status_code == 200
    assert db.session.get(Order, theirs.id).board_position is None


def test_orders_list_includes_board_position(client, user, auth):
    _order(user, 1, board_position=3.0)
    assert client.get('/api/orders', headers=auth).get_json()['orders'][0]['board_position'] == 3.0


# ---------- Etsy shipped -> Shipped column ----------

class FakeEtsyAPI:
    def __init__(self, receipts):
        self.receipts = receipts

    def get_shop_receipts(self, shop_id, **params):
        return {'results': self.receipts if params.get('offset', 0) == 0 else [], 'count': len(self.receipts)}

    def get_receipt_transactions(self, shop_id, receipt_id):
        return {'results': []}


def _receipt(receipt_id, status):
    now = int(datetime.now(timezone.utc).timestamp())
    return {'receipt_id': receipt_id, 'status': status, 'buyer_email': f'{receipt_id}@example.com', 'name': 'B',
            'create_timestamp': now, 'update_timestamp': now, 'grandtotal': {'amount': 1000, 'currency_code': 'USD'}}


def _sync(user, *receipts):
    result = OrderSyncManager.sync_orders_from_etsy(user, 'shop1', FakeEtsyAPI(list(receipts)))
    assert result['success'], result


def _by_receipt(receipt_id):
    return Order.query.filter_by(etsy_order_id=str(receipt_id)).one()


def test_new_orders_already_completed_on_etsy_start_in_shipped(app, user):
    _sync(user, _receipt(1, 'completed'), _receipt(2, 'paid'))
    assert _by_receipt(1).production_status == 'SHIPPED'
    assert _by_receipt(2).production_status == 'QUEUED'


def test_order_moves_to_shipped_when_etsy_completes_it(app, user):
    _sync(user, _receipt(1, 'paid'))
    order = _by_receipt(1)
    order.production_status = 'PRINTED'
    db.session.commit()

    _sync(user, _receipt(1, 'completed'))
    assert _by_receipt(1).production_status == 'SHIPPED'


def test_old_completed_orders_left_in_queued_get_moved(app, user):
    old = _order(user, 1, status='COMPLETED', production_status='QUEUED')   # synced before this rule existed
    old.etsy_order_id = '501'
    db.session.commit()
    _sync(user, _receipt(501, 'completed'))
    assert _by_receipt(501).production_status == 'SHIPPED'


def test_completed_order_moved_back_for_a_reprint_stays_put(app, user):
    _sync(user, _receipt(1, 'completed'))
    order = _by_receipt(1)
    order.production_status = 'PRINTING'      # reprinting it
    order.print_started_at = utcnow()
    db.session.commit()

    _sync(user, _receipt(1, 'completed'))
    assert _by_receipt(1).production_status == 'PRINTING'
