"""Production must not sign login tokens with a missing or publicly known SECRET_KEY."""
import pytest

from config import check_secret_key


@pytest.mark.parametrize('key', [None, '', 'dev-secret-key', 'dev-secret-change-in-production'])
def test_production_refuses_missing_or_placeholder_key(key):
    with pytest.raises(RuntimeError):
        check_secret_key(key, production=True)


def test_development_only_warns_on_placeholder():
    assert 'placeholder' in check_secret_key('dev-secret-key', production=False)


def test_short_key_warns_but_starts():
    assert '32' in check_secret_key('x' * 20, production=True)


def test_good_key_is_quiet():
    assert check_secret_key('a' * 64, production=True) is None


def test_create_app_production_refuses_placeholder(monkeypatch):
    from app import create_app
    from config import ProductionConfig
    monkeypatch.setattr(ProductionConfig, 'SECRET_KEY', 'dev-secret-key')
    with pytest.raises(RuntimeError):
        create_app('production')
