"""Broad, shallow coverage of the API: auth on every route, empty-state reads, basic CRUD,
validation and per-user isolation. A safety net for refactoring app.py, not a substitute for
focused tests of each area."""
import re
from datetime import datetime, timedelta

import pytest

from models import db, Customer, Filament, Order, OrderItem, Printer, ProductProfile, ScheduledPrint, User

# Routes that are deliberately reachable without a user session.
PUBLIC = {'/api/health', '/api/auth/login', '/api/auth/callback', '/api/auth/refresh'}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """These tests must never reach Etsy, Manyfold or a printer."""
    def blocked(*args, **kwargs):
        raise AssertionError('test tried to make a network request')
    monkeypatch.setattr('requests.sessions.Session.request', blocked)


@pytest.fixture
def auth(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
def other_user(app):
    u = User(etsy_user_id='999', username='other', access_token='tok', shop_id='shop2')
    db.session.add(u)
    db.session.commit()
    return u


def _concrete(rule):
    return re.sub(r'<[^>]+>', '1', rule)


def _order(user, n=1, **kwargs):
    order = Order(user_id=user.id, etsy_order_id=f'{user.id}-{n}', etsy_shop_id='shop', buyer_name='Buyer',
                  total_amount=20.0, currency='USD', status='PENDING', created_at=datetime.utcnow() - timedelta(days=n),
                  production_status='QUEUED', **kwargs)
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderItem(order_id=order.id, title='Dragon', quantity=1, price=20.0))
    db.session.commit()
    return order


# ---------- auth ----------

def test_every_non_public_route_requires_auth(app, client):
    checked = 0
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith('/api/') or rule.rule in PUBLIC:
            continue
        for method in rule.methods - {'HEAD', 'OPTIONS'}:
            resp = client.open(_concrete(rule.rule), method=method)
            assert resp.status_code == 401, f'{method} {rule.rule} answered {resp.status_code} without auth'
            checked += 1
    assert checked > 80   # guards against the loop silently matching nothing


def test_garbage_token_is_rejected(client):
    assert client.get('/api/orders', headers={'Authorization': 'Bearer not-a-token'}).status_code == 401


# ---------- empty-state reads ----------

@pytest.mark.parametrize('path', [
    '/api/orders', '/api/customers', '/api/customers/segments', '/api/filaments', '/api/product-profiles',
    '/api/printers', '/api/printers/utilization', '/api/printers/maintenance', '/api/printer-connections',
    '/api/production/queue', '/api/print-sessions', '/api/files', '/api/devices',
    '/api/analytics/summary', '/api/analytics/revenue-trends', '/api/analytics/product-performance',
    '/api/alerts/settings', '/api/alerts/preview', '/api/integrations/manyfold/settings', '/api/auth/user',
])
def test_reads_work_for_a_new_account(client, user, auth, path):
    resp = client.get(path, headers=auth)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json() is not None


# ---------- missing resources are 404, not 500 ----------

@pytest.mark.parametrize('method,path', [
    ('GET', '/api/orders/999'), ('GET', '/api/printers/999'), ('GET', '/api/print-sessions/999'),
    ('GET', '/api/files/999'), ('GET', '/api/orders/999/notes'), ('GET', '/api/printer-connections/999/status'),
    ('PUT', '/api/filaments/999'), ('DELETE', '/api/filaments/999'),
    ('PUT', '/api/product-profiles/999'), ('DELETE', '/api/product-profiles/999'),
    ('PUT', '/api/orders/999/production-status'), ('DELETE', '/api/devices/999'),
    ('GET', '/api/bambu/materials/999'), ('POST', '/api/bambu/materials/999'), ('PUT', '/api/bambu/materials/999'),
    ('GET', '/api/bambu/notifications/999'), ('PUT', '/api/bambu/notifications/999'),
    ('GET', '/api/bambu/scheduled-prints/999'), ('GET', '/api/bambu/scheduled-prints/999/queue'),
    ('PUT', '/api/bambu/scheduled-prints/999'), ('DELETE', '/api/bambu/scheduled-prints/999'),
    ('POST', '/api/orders/999/schedule-prints'),
])
def test_missing_resource_is_404(client, user, auth, method, path):
    resp = client.open(path, method=method, headers=auth, json={} if method != 'GET' else None)
    assert resp.status_code == 404, f'{method} {path} answered {resp.status_code}'


def test_scheduling_a_print_on_a_missing_printer_is_404(client, user, auth):
    resp = client.post('/api/bambu/scheduled-prints', headers=auth, json={'printer_id': 999, 'job_name': 'x'})
    assert resp.status_code == 404


# ---------- filaments ----------

def test_filament_crud(client, user, auth):
    created = client.post('/api/filaments', headers=auth, json={
        'material': 'PLA', 'color': 'Black', 'initial_amount': 1000, 'current_amount': 800, 'cost_per_gram': 0.02})
    assert created.status_code == 201
    fid = created.get_json()['id']

    listed = client.get('/api/filaments', headers=auth).get_json()
    assert [f['color'] for f in listed['filaments']] == ['Black']

    updated = client.put(f'/api/filaments/{fid}', headers=auth, json={'current_amount': 50})
    assert updated.status_code == 200
    assert Filament.query.get(fid).current_amount == 50

    assert client.delete(f'/api/filaments/{fid}', headers=auth).status_code == 200
    assert Filament.query.count() == 0


@pytest.mark.parametrize('body', [{}, {'material': 'PLA'}, {'color': 'Black'}])
def test_filament_requires_material_and_color(client, user, auth, body):
    resp = client.post('/api/filaments', headers=auth, json=body)
    assert resp.status_code == 400
    assert Filament.query.count() == 0


# ---------- product profiles ----------

def test_product_profile_crud(client, user, auth):
    created = client.post('/api/product-profiles', headers=auth, json={
        'product_name': 'Dragon', 'standard_filament_amount': 85, 'print_time_minutes': 240})
    assert created.status_code == 201
    pid = created.get_json()['id']

    assert client.put(f'/api/product-profiles/{pid}', headers=auth, json={'category': 'Toys'}).status_code == 200
    assert ProductProfile.query.get(pid).category == 'Toys'

    assert client.delete(f'/api/product-profiles/{pid}', headers=auth).status_code == 200
    assert ProductProfile.query.count() == 0


def test_product_profile_requires_a_name(client, user, auth):
    assert client.post('/api/product-profiles', headers=auth, json={'standard_filament_amount': 10}).status_code == 400
    assert ProductProfile.query.count() == 0


# ---------- printers ----------

def test_printer_crud(client, user, auth):
    assert client.post('/api/printers', headers=auth, json={}).status_code == 400
    created = client.post('/api/printers', headers=auth, json={'name': 'X1 Carbon', 'model': 'Bambu X1C'})
    assert created.status_code == 201
    pid = created.get_json()['id']

    assert client.get(f'/api/printers/{pid}', headers=auth).get_json()['name'] == 'X1 Carbon'
    assert client.put(f'/api/printers/{pid}', headers=auth, json={'status': 'MAINTENANCE'}).status_code == 200
    assert Printer.query.get(pid).status == 'MAINTENANCE'
    assert client.delete(f'/api/printers/{pid}', headers=auth).status_code == 200
    assert Printer.query.count() == 0


# ---------- orders and production ----------

def test_orders_list_and_filter_by_production_status(client, user, auth):
    _order(user, 1)
    printing = _order(user, 2)
    printing.production_status = 'PRINTING'
    db.session.commit()

    assert client.get('/api/orders', headers=auth).get_json()['total'] == 2
    filtered = client.get('/api/orders?production_status=PRINTING', headers=auth).get_json()
    assert [o['id'] for o in filtered['orders']] == [printing.id]
    assert filtered['orders'][0]['items'][0]['title'] == 'Dragon'


def test_production_status_flow(client, user, auth):
    order = _order(user)
    url = f'/api/orders/{order.id}/production-status'

    assert client.put(url, headers=auth, json={'production_status': 'NOPE'}).status_code == 400
    assert client.put(url, headers=auth, json={'production_status': 'PRINTING'}).status_code == 200
    assert Order.query.get(order.id).print_started_at is not None
    # Back-date the start so the recorded print time is checkable.
    Order.query.get(order.id).print_started_at = datetime.utcnow() - timedelta(minutes=90)
    db.session.commit()
    assert client.put(url, headers=auth, json={'production_status': 'PRINTED'}).status_code == 200
    done = Order.query.get(order.id)
    assert done.print_completed_at is not None
    assert done.actual_print_time in (89, 90)

    queue = client.get('/api/production/queue', headers=auth).get_json()
    assert queue['total'] >= 1


def test_customer_create_and_list(client, user, auth):
    created = client.post('/api/customers', headers=auth, json={'name': 'Ava', 'email': 'ava@example.com'})
    assert created.status_code == 201
    assert client.get('/api/customers', headers=auth).get_json()['total'] == 1


# ---------- one user can never see or change another's data ----------

def test_lists_only_show_own_records(client, user, auth, other_user):
    _order(other_user)
    db.session.add_all([
        Filament(user_id=other_user.id, material='PLA', color='Red', initial_amount=1000, current_amount=1000),
        Printer(user_id=other_user.id, name='Theirs'),
        ProductProfile(user_id=other_user.id, product_name='Theirs', standard_filament_amount=10),
        Customer(user_id=other_user.id, name='Theirs'),
    ])
    db.session.commit()

    for path, key in [('/api/orders', 'orders'), ('/api/filaments', 'filaments'), ('/api/printers', 'printers'),
                      ('/api/product-profiles', 'profiles'), ('/api/customers', 'customers'),
                      ('/api/production/queue', 'orders')]:
        body = client.get(path, headers=auth).get_json()
        assert body[key] == [], f'{path} leaked another user\'s records'


def test_cannot_read_or_change_another_users_records(client, user, auth, other_user):
    order = _order(other_user)
    filament = Filament(user_id=other_user.id, material='PLA', color='Red', initial_amount=1000, current_amount=1000)
    printer = Printer(user_id=other_user.id, name='Theirs')
    profile = ProductProfile(user_id=other_user.id, product_name='Theirs', standard_filament_amount=10)
    db.session.add_all([filament, printer, profile])
    db.session.flush()
    job = ScheduledPrint(user_id=other_user.id, printer_id=printer.id, job_name='theirs')
    db.session.add(job)
    db.session.commit()

    attempts = [
        ('GET', f'/api/orders/{order.id}'), ('PUT', f'/api/orders/{order.id}/production-status'),
        ('PUT', f'/api/orders/{order.id}/priority'), ('POST', f'/api/orders/{order.id}/shipping-label'),
        ('PUT', f'/api/filaments/{filament.id}'), ('DELETE', f'/api/filaments/{filament.id}'),
        ('GET', f'/api/printers/{printer.id}'), ('PUT', f'/api/printers/{printer.id}'), ('DELETE', f'/api/printers/{printer.id}'),
        ('PUT', f'/api/product-profiles/{profile.id}'), ('DELETE', f'/api/product-profiles/{profile.id}'),
        ('GET', f'/api/bambu/materials/{printer.id}'), ('GET', f'/api/bambu/scheduled-prints/{printer.id}'),
        ('PUT', f'/api/bambu/scheduled-prints/{job.id}'), ('DELETE', f'/api/bambu/scheduled-prints/{job.id}'),
    ]
    for method, path in attempts:
        resp = client.open(path, method=method, headers=auth,
                           json={'production_status': 'PRINTED', 'priority': 1, 'status': 'IDLE'} if method != 'GET' else None)
        assert resp.status_code in (403, 404), f'{method} {path} answered {resp.status_code} for another user\'s record'

    assert Order.query.get(order.id).production_status == 'QUEUED'
    assert Filament.query.count() == 1 and Printer.query.count() == 1 and ProductProfile.query.count() == 1
    assert ScheduledPrint.query.count() == 1
