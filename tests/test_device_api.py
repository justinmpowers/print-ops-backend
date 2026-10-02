from models import db, DeviceKey, Printer, PrinterConnection, ScheduledPrint


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _device_key(client, token, name='PrintHub'):
    resp = client.post('/api/devices', json={'name': name}, headers=_auth(token))
    assert resp.status_code == 201
    return resp.get_json()


def _printer(user, name='X1 Carbon', serial=None):
    printer = Printer(user_id=user.id, name=name, status='IDLE')
    db.session.add(printer)
    db.session.flush()
    if serial:
        db.session.add(PrinterConnection(printer_id=printer.id, user_id=user.id, connection_type='bambu_lan',
                                         api_url='http://192.168.1.50', serial_number=serial))
    db.session.commit()
    return printer


def _event(client, key, **body):
    return client.post('/api/device/printer-events', json=body, headers={'X-Device-Key': key})


def test_create_device_key_returns_raw_key_once_and_stores_only_hash(client, user, token):
    created = _device_key(client, token)
    assert created['key'].startswith('pok_')
    assert created['key_prefix'] == created['key'][:10]

    record = DeviceKey.query.one()
    assert record.key_hash != created['key']
    assert created['key'] not in (record.key_hash, record.key_prefix)

    listed = client.get('/api/devices', headers=_auth(token)).get_json()
    assert listed['total'] == 1
    assert 'key' not in listed['devices'][0]


def test_create_device_key_requires_name(client, user, token):
    assert client.post('/api/devices', json={}, headers=_auth(token)).status_code == 400


def test_device_endpoints_reject_missing_wrong_and_user_tokens(client, user, token):
    assert client.get('/api/device/ping').status_code == 401
    assert client.get('/api/device/ping', headers={'X-Device-Key': 'pok_nope'}).status_code == 401
    assert client.get('/api/device/ping', headers={'X-Device-Key': token}).status_code == 401
    # and a device key is not a user session
    key = _device_key(client, token)['key']
    assert client.get('/api/devices', headers={'Authorization': f'Bearer {key}'}).status_code == 401


def test_ping_works_and_records_last_used(client, user, token):
    key = _device_key(client, token)['key']
    resp = client.get('/api/device/ping', headers={'X-Device-Key': key})
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'device': 'PrintHub'}
    assert DeviceKey.query.one().last_used_at is not None


def test_revoked_key_stops_working(client, user, token):
    created = _device_key(client, token)
    assert client.delete(f"/api/devices/{created['id']}", headers=_auth(token)).status_code == 200
    assert client.get('/api/device/ping', headers={'X-Device-Key': created['key']}).status_code == 401


def test_cannot_revoke_another_users_key(client, user, token):
    from models import User
    other = User(etsy_user_id='999', username='other', access_token='tok', shop_id='shop2')
    db.session.add(other)
    db.session.commit()
    record = DeviceKey(user_id=other.id, name='theirs', key_hash='x' * 64, key_prefix='pok_xxxxxx')
    db.session.add(record)
    db.session.commit()
    assert client.delete(f'/api/devices/{record.id}', headers=_auth(token)).status_code == 404
    assert DeviceKey.query.get(record.id).revoked_at is None


def test_started_then_finished_records_a_print_and_updates_printer(client, user, token):
    key = _device_key(client, token)['key']
    printer = _printer(user, serial='AC12345')

    resp = _event(client, key, printer={'name': 'whatever', 'serial_number': 'AC12345'},
                  event='started', job_name='benchy')
    assert resp.status_code == 200
    job = ScheduledPrint.query.one()
    assert (job.status, job.job_name, job.printer_id) == ('started', 'benchy', printer.id)
    assert job.started_at is not None
    assert Printer.query.get(printer.id).status == 'PRINTING'
    assert printer.connection.status == 'connected'

    resp = _event(client, key, printer={'serial_number': 'AC12345'}, event='finished', job_name='benchy')
    assert resp.status_code == 200
    assert ScheduledPrint.query.count() == 1
    job = ScheduledPrint.query.one()
    assert job.status == 'completed' and job.completed_at is not None
    assert Printer.query.get(printer.id).status == 'IDLE'


def test_failed_event_records_reason(client, user, token):
    key = _device_key(client, token)['key']
    _printer(user, name='Ender')
    _event(client, key, printer={'name': 'ender'}, event='started', job_name='bracket')
    resp = _event(client, key, printer={'name': 'ENDER'}, event='failed', job_name='bracket', message='Cancelled')
    assert resp.status_code == 200
    job = ScheduledPrint.query.one()
    assert (job.status, job.failed_reason) == ('failed', 'Cancelled')


def test_started_claims_a_matching_queued_print(client, user, token):
    key = _device_key(client, token)['key']
    printer = _printer(user)
    queued = ScheduledPrint(user_id=user.id, printer_id=printer.id, job_name='Order 12', file_name='dragon', status='queued')
    db.session.add(queued)
    db.session.commit()

    _event(client, key, printer={'name': 'X1 Carbon'}, event='started', job_name='dragon')
    assert ScheduledPrint.query.count() == 1
    assert ScheduledPrint.query.one().status == 'started'


def test_finished_without_a_started_print_still_records_history(client, user, token):
    key = _device_key(client, token)['key']
    _printer(user)
    resp = _event(client, key, printer={'name': 'X1 Carbon'}, event='finished', job_name='late')
    assert resp.status_code == 200
    assert ScheduledPrint.query.one().status == 'completed'


def test_new_start_closes_a_stale_open_print(client, user, token):
    key = _device_key(client, token)['key']
    _printer(user)
    _event(client, key, printer={'name': 'X1 Carbon'}, event='started', job_name='first')
    _event(client, key, printer={'name': 'X1 Carbon'}, event='started', job_name='second')
    statuses = {j.job_name: j.status for j in ScheduledPrint.query.all()}
    assert statuses == {'first': 'completed', 'second': 'started'}


def test_unknown_printer_and_bad_event(client, user, token):
    key = _device_key(client, token)['key']
    _printer(user)
    assert _event(client, key, printer={'name': 'nope'}, event='started').status_code == 404
    assert _event(client, key, printer={'name': 'X1 Carbon'}, event='exploded').status_code == 400
    assert ScheduledPrint.query.count() == 0


def test_device_cannot_touch_another_users_printer(client, user, token):
    from models import User
    other = User(etsy_user_id='999', username='other', access_token='tok', shop_id='shop2')
    db.session.add(other)
    db.session.commit()
    _printer(other, name='Theirs', serial='ZZ999')
    key = _device_key(client, token)['key']
    assert _event(client, key, printer={'name': 'Theirs', 'serial_number': 'ZZ999'}, event='started').status_code == 404
