"""Spool codes and the spool page a QR label opens: looking a spool up, weighing it, marking it empty."""
import pytest

from models import db, Filament, FilamentUsage, Order, User


@pytest.fixture
def auth(token):
    return {'Authorization': f'Bearer {token}'}


def _spool(user, **kwargs):
    values = dict(user_id=user.id, material='PLA', color='Galaxy Black', initial_amount=1000, current_amount=1000)
    values.update(kwargs)
    spool = Filament(**values)
    db.session.add(spool)
    db.session.commit()
    return spool


def test_every_spool_has_a_stable_code(app, user):
    spool = _spool(user)
    assert spool.code == f'F-{spool.id:04d}'
    assert spool.to_dict()['code'] == spool.code


@pytest.mark.parametrize('text,expected', [
    ('F-0042', 42), ('f-42', 42), (' F-0042 ', 42), ('42', 42), ('F-', None), ('X-42', None), ('', None), (None, None),
])
def test_codes_parse_back_to_ids(text, expected):
    assert Filament.id_from_code(text) == expected


def test_create_and_update_keep_brand_location_and_spool_weight(client, user, auth):
    resp = client.post('/api/filaments', headers=auth, json={
        'material': 'PETG', 'color': 'Orange', 'initial_amount': 1000, 'current_amount': 1000,
        'brand': ' Polymaker ', 'location': 'Dry box 2', 'spool_weight': '210', 'low_stock_threshold': 150})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    created = resp.get_json()
    assert created['brand'] == 'Polymaker'
    assert created['location'] == 'Dry box 2'
    assert created['spool_weight'] == 210.0
    assert created['low_stock_threshold'] == 150.0     # used to be dropped on create
    assert created['code'].startswith('F-')

    resp = client.put(f"/api/filaments/{created['id']}", headers=auth, json={'location': '', 'spool_weight': None})
    assert resp.status_code == 200
    assert resp.get_json()['location'] is None
    assert resp.get_json()['spool_weight'] is None


@pytest.mark.parametrize('bad', ['heavy', 'nan', 'inf'])
def test_non_numeric_spool_weight_is_a_400(client, user, auth, bad):
    resp = client.post('/api/filaments', headers=auth, json={'material': 'PLA', 'color': 'Red', 'spool_weight': bad})
    assert resp.status_code == 400


def test_spool_page_shows_recent_usage_newest_first(client, user, auth):
    spool = _spool(user)
    order = Order(user_id=user.id, etsy_order_id='3001', etsy_shop_id='s')
    db.session.add(order)
    db.session.commit()
    db.session.add_all([FilamentUsage(filament_id=spool.id, amount_used=12, description='first'),
                        FilamentUsage(filament_id=spool.id, order_id=order.id, amount_used=30, description='second')])
    db.session.commit()

    resp = client.get(f'/api/filaments/{spool.id}', headers=auth)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['code'] == spool.code
    assert [u['description'] for u in body['usage']] == ['second', 'first']
    assert body['usage'][0]['order_etsy_id'] == '3001'


def test_adjust_sets_remaining_and_logs_the_difference(client, user, auth):
    spool = _spool(user, current_amount=800)

    resp = client.post(f'/api/filaments/{spool.id}/adjust', headers=auth,
                       json={'current_amount': 650, 'note': 'Weighed on scale: 860 g'})
    assert resp.status_code == 200
    assert resp.get_json()['current_amount'] == 650
    log = FilamentUsage.query.filter_by(filament_id=spool.id).one()
    assert log.amount_used == 150
    assert log.description == 'Weighed on scale: 860 g'

    # Mark empty
    assert client.post(f'/api/filaments/{spool.id}/adjust', headers=auth, json={'current_amount': 0}).status_code == 200
    assert db.session.get(Filament, spool.id).current_amount == 0
    assert FilamentUsage.query.filter_by(filament_id=spool.id).count() == 2

    # Same amount again: nothing to log
    client.post(f'/api/filaments/{spool.id}/adjust', headers=auth, json={'current_amount': 0})
    assert FilamentUsage.query.filter_by(filament_id=spool.id).count() == 2


@pytest.mark.parametrize('amount', [-5, 'lots', None, 'nan'])
def test_adjust_rejects_bad_amounts(client, user, auth, amount):
    spool = _spool(user)
    resp = client.post(f'/api/filaments/{spool.id}/adjust', headers=auth, json={'current_amount': amount})
    assert resp.status_code == 400
    assert db.session.get(Filament, spool.id).current_amount == 1000


def test_spool_pages_are_private(client, user, auth):
    other = User(etsy_user_id='999', username='other', access_token='tok', shop_id='shop2')
    db.session.add(other)
    db.session.commit()
    theirs = _spool(other)
    assert client.get(f'/api/filaments/{theirs.id}', headers=auth).status_code == 404
    assert client.post(f'/api/filaments/{theirs.id}/adjust', headers=auth, json={'current_amount': 0}).status_code == 404
    assert db.session.get(Filament, theirs.id).current_amount == 1000
