from unittest.mock import patch, MagicMock

from models import db, AlertSettings, Filament


def _auth(token):
    return {'Authorization': f'Bearer {token}'}


def _low_stock_filament(user):
    db.session.add(Filament(user_id=user.id, material='PLA', color='Black', initial_amount=1000,
                            current_amount=10, low_stock_threshold=100))
    db.session.commit()


def test_settings_round_trip_ntfy_fields(client, user, token):
    resp = client.put('/api/alerts/settings', headers=_auth(token), json={
        'ntfy_server': 'https://ntfy.example.com', 'ntfy_topic': 'shop-alerts', 'ntfy_token': 'tk_abc'})
    assert resp.status_code == 200
    body = client.get('/api/alerts/settings', headers=_auth(token)).get_json()
    assert (body['ntfy_server'], body['ntfy_topic'], body['ntfy_token']) == ('https://ntfy.example.com', 'shop-alerts', 'tk_abc')
    assert body['ntfy_enabled'] is True
    assert not any(k.startswith('telegram') for k in body)


def test_settings_reject_bad_topic_and_server(client, user, token):
    assert client.put('/api/alerts/settings', headers=_auth(token), json={'ntfy_topic': 'a/b'}).status_code == 400
    assert client.put('/api/alerts/settings', headers=_auth(token),
                      json={'ntfy_server': 'ftp://ntfy.example.com'}).status_code == 400
    assert client.put('/api/alerts/settings', headers=_auth(token),
                      json={'ntfy_server': 'http://169.254.169.254'}).status_code == 400
    assert AlertSettings.query.filter(AlertSettings.ntfy_topic.isnot(None)).count() == 0


def test_trigger_sends_to_ntfy_with_default_server(client, user, token):
    _low_stock_filament(user)
    client.put('/api/alerts/settings', headers=_auth(token), json={'ntfy_topic': 'shop-alerts'})

    with patch('app.requests.post', return_value=MagicMock(status_code=200)) as post:
        resp = client.post('/api/alerts/trigger', headers=_auth(token))

    assert resp.status_code == 200
    assert resp.get_json()['channels'] == ['ntfy']
    (url,), kwargs = post.call_args
    assert url == 'https://ntfy.sh/shop-alerts'
    assert b'Black' in kwargs['data']
    assert 'Authorization' not in kwargs['headers']


def test_trigger_sends_token_to_custom_server(client, user, token):
    _low_stock_filament(user)
    client.put('/api/alerts/settings', headers=_auth(token), json={
        'ntfy_server': 'https://ntfy.example.com/', 'ntfy_topic': 'shop-alerts', 'ntfy_token': 'tk_abc'})

    with patch('app.requests.post', return_value=MagicMock(status_code=200)) as post:
        client.post('/api/alerts/trigger', headers=_auth(token))

    (url,), kwargs = post.call_args
    assert url == 'https://ntfy.example.com/shop-alerts'
    assert kwargs['headers']['Authorization'] == 'Bearer tk_abc'


def test_trigger_without_ntfy_topic_sends_nothing(client, user, token):
    _low_stock_filament(user)
    with patch('app.requests.post') as post:
        resp = client.post('/api/alerts/trigger', headers=_auth(token))
    assert resp.get_json()['channels'] == []
    post.assert_not_called()
