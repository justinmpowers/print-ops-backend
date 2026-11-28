"""'Keep me signed in': how long a login session lasts, and what happens when Etsy stops accepting
the stored Etsy login."""
from datetime import timedelta

import pytest

from authentication import EtsyOAuth, EtsyTokenRefreshError, TokenManager
from models import utcnow, db, RefreshToken, User


def _refresh_row(raw_token):
    return RefreshToken.query.filter_by(token_hash=TokenManager._hash_refresh_token(raw_token)).one()


def _days_left(row):
    return (row.expires_at - utcnow()) / timedelta(days=1)


def test_plain_session_lasts_the_normal_refresh_lifetime(app, user):
    _, refresh = TokenManager.create_token_pair(user.id)
    row = _refresh_row(refresh)
    assert row.remember_me is False
    assert _days_left(row) == pytest.approx(app.config['REFRESH_TOKEN_EXPIRATION_DAYS'], abs=0.01)


def test_remembered_session_lasts_longer_and_keeps_doing_so_after_refresh(app, client, user):
    _, refresh = TokenManager.create_token_pair(user.id, remember_me=True)
    assert _days_left(_refresh_row(refresh)) == pytest.approx(app.config['REMEMBER_ME_REFRESH_TOKEN_DAYS'], abs=0.01)

    resp = client.post('/api/auth/refresh', json={'refresh_token': refresh})
    assert resp.status_code == 200
    rotated = _refresh_row(resp.get_json()['refresh_token'])
    assert rotated.remember_me is True
    assert _days_left(rotated) == pytest.approx(app.config['REMEMBER_ME_REFRESH_TOKEN_DAYS'], abs=0.01)


@pytest.fixture
def fake_etsy_login(monkeypatch):
    monkeypatch.setattr(EtsyOAuth, 'exchange_code_for_token', staticmethod(
        lambda code, verifier=None: {'access_token': '777.abc', 'refresh_token': 'etsy-r', 'expires_in': 3600,
                                     'user_id': '777'}))
    monkeypatch.setattr(EtsyOAuth, 'get_shop_for_user', staticmethod(lambda uid, tok: {'shop_id': 5, 'shop_name': 'S'}))
    monkeypatch.setattr(EtsyOAuth, 'get_user_profile', staticmethod(lambda tok, uid: {'first_name': 'Sam'}))


@pytest.mark.parametrize('sent,expected', [(True, True), (False, False), (None, False), ('yes', False)])
def test_login_callback_honours_keep_me_signed_in(client, fake_etsy_login, sent, expected):
    body = {'code': 'c', 'code_verifier': 'v'}
    if sent is not None:
        body['remember_me'] = sent
    resp = client.post('/api/auth/callback', json=body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data['remember_me'] is expected
    assert _refresh_row(data['refresh_token']).remember_me is expected


# ---------- the Etsy side of staying signed in ----------

@pytest.fixture
def expired_etsy_token(user):
    user.token_expires_at = utcnow() - timedelta(minutes=5)
    user.refresh_token = 'old-etsy-refresh'
    db.session.commit()
    return user


def _fail_refresh(monkeypatch, rejected):
    def refuse(token):
        raise EtsyTokenRefreshError('nope', rejected=rejected)
    monkeypatch.setattr(EtsyOAuth, 'refresh_access_token', staticmethod(refuse))


@pytest.mark.parametrize('path', ['/api/orders/sync', '/api/products/sync-etsy'])
def test_rejected_etsy_login_asks_for_reconnect_without_ending_the_session(client, token, expired_etsy_token,
                                                                          monkeypatch, path):
    _fail_refresh(monkeypatch, rejected=True)
    resp = client.post(path, headers={'Authorization': f'Bearer {token}'})
    # Not 401: that would make the app think the PrintOps session ended and log the user out.
    assert resp.status_code == 409
    assert resp.get_json()['needs_reconnect'] is True


def test_etsy_unreachable_is_temporary_not_a_reconnect(client, token, expired_etsy_token, monkeypatch):
    _fail_refresh(monkeypatch, rejected=False)
    resp = client.post('/api/orders/sync', headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 503
    assert 'needs_reconnect' not in resp.get_json()


def test_etsy_token_about_to_expire_is_renewed_before_use(client, token, user, monkeypatch):
    user.token_expires_at = utcnow() + timedelta(seconds=30)
    user.refresh_token = 'old-etsy-refresh'
    db.session.commit()
    monkeypatch.setattr(EtsyOAuth, 'refresh_access_token', staticmethod(
        lambda tok: {'access_token': 'new-etsy-access', 'refresh_token': 'new-etsy-refresh', 'expires_in': 3600}))
    monkeypatch.setattr('etsy_api.OrderSyncManager.sync_orders_from_etsy', staticmethod(
        lambda *a, **k: {'success': True, 'message': 'ok'}))

    assert client.post('/api/orders/sync', headers={'Authorization': f'Bearer {token}'}).status_code == 200
    refreshed = db.session.get(User, user.id)
    assert refreshed.access_token == 'new-etsy-access'
    assert refreshed.refresh_token == 'new-etsy-refresh'


def test_missing_etsy_refresh_token_counts_as_rejected(app):
    with pytest.raises(EtsyTokenRefreshError) as err:
        EtsyOAuth.refresh_access_token(None)
    assert err.value.rejected is True
