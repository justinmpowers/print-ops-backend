from models import db, Printer, PrinterConnection, PrinterLiveStatus


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _key(client, token):
    return client.post('/api/devices', json={'name': 'PrintHub'}, headers=_auth(token)).get_json()['key']


def _post(client, key, printers):
    return client.post('/api/device/printer-status', json={'printers': printers}, headers={'X-Device-Key': key})


def _printer(user, name='X1 Carbon', serial=None):
    p = Printer(user_id=user.id, name=name, status='IDLE')
    db.session.add(p)
    db.session.flush()
    if serial:
        db.session.add(PrinterConnection(printer_id=p.id, user_id=user.id, connection_type='bambu_lan',
                                         api_url='http://192.168.1.50', serial_number=serial))
    db.session.commit()
    return p


X1 = {'name': 'X1 Carbon', 'serial_number': 'SN1', 'state': 'printing', 'job_name': 'benchy',
      'progress': 42.5, 'remaining_minutes': 37, 'nozzle_temp': 220.4, 'bed_temp': 60, 'message': ''}


def test_status_is_stored_and_shown_on_the_printer(client, user, token):
    key = _key(client, token)
    p = _printer(user, serial='SN1')

    resp = _post(client, key, [X1])
    assert resp.status_code == 200
    assert resp.get_json() == {'matched': ['X1 Carbon'], 'unknown': []}

    body = client.get(f'/api/printers/{p.id}', headers=_auth(token)).get_json()
    live = body['live_status']
    assert (live['state'], live['job_name'], live['progress'], live['remaining_minutes']) == ('printing', 'benchy', 42.5, 37)
    assert live['nozzle_temp'] == 220.4 and live['source'] == 'PrintHub' and live['age_seconds'] >= 0
    assert body['status'] == 'PRINTING'
    assert body['connection_status'] == 'connected'


def test_updates_replace_the_previous_status(client, user, token):
    key = _key(client, token)
    _printer(user)
    _post(client, key, [X1])
    _post(client, key, [{**X1, 'state': 'finished', 'progress': 100, 'remaining_minutes': 0}])
    assert PrinterLiveStatus.query.count() == 1
    live = PrinterLiveStatus.query.one()
    assert (live.state, live.progress) == ('finished', 100)
    assert Printer.query.one().status == 'IDLE'


def test_state_mapping_and_maintenance_is_kept(client, user, token):
    key = _key(client, token)
    p = _printer(user)
    for state, expected in [('offline', 'OFFLINE'), ('error', 'ERROR'), ('failed', 'IDLE'), ('paused', 'PRINTING')]:
        _post(client, key, [{**X1, 'state': state}])
        assert Printer.query.get(p.id).status == expected, state
    Printer.query.get(p.id).status = 'MAINTENANCE'
    db.session.commit()
    _post(client, key, [{**X1, 'state': 'printing'}])
    assert Printer.query.get(p.id).status == 'MAINTENANCE'


def test_unknown_printers_are_reported_not_fatal(client, user, token):
    key = _key(client, token)
    _printer(user)
    resp = _post(client, key, [X1, {**X1, 'name': 'Ghost', 'serial_number': 'nope'}])
    assert resp.get_json() == {'matched': ['X1 Carbon'], 'unknown': ['Ghost']}


def test_bad_values_are_tolerated(client, user, token):
    key = _key(client, token)
    _printer(user)
    resp = _post(client, key, [{'name': 'X1 Carbon', 'state': 'printing', 'progress': 'abc', 'nozzle_temp': None}, 'junk'])
    assert resp.status_code == 200
    live = PrinterLiveStatus.query.one()
    assert live.progress is None and live.nozzle_temp is None


def test_requires_a_list_and_a_device_key(client, user, token):
    key = _key(client, token)
    assert client.post('/api/device/printer-status', json={'printers': 'x'}, headers={'X-Device-Key': key}).status_code == 400
    assert client.post('/api/device/printer-status', json={'printers': []}).status_code == 401
    assert client.post('/api/device/printer-status', json={'printers': []}, headers=_auth(token)).status_code == 401


def test_cannot_update_another_users_printer(client, user, token):
    from models import User
    other = User(etsy_user_id='999', username='other', access_token='tok', shop_id='shop2')
    db.session.add(other)
    db.session.commit()
    _printer(other, serial='SN1')
    key = _key(client, token)
    assert _post(client, key, [X1]).get_json() == {'matched': [], 'unknown': ['X1 Carbon']}
    assert PrinterLiveStatus.query.count() == 0


def test_deleting_a_printer_removes_its_live_status(client, user, token):
    key = _key(client, token)
    p = _printer(user)
    _post(client, key, [X1])
    assert client.delete(f'/api/printers/{p.id}', headers=_auth(token)).status_code == 200
    assert PrinterLiveStatus.query.count() == 0
