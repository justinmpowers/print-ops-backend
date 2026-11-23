"""Production must not sign login tokens with a missing or publicly known SECRET_KEY."""
import pytest

from config import token_signing_issue, SIGNING_ISSUE_MESSAGES


@pytest.mark.parametrize('key', [None, '', 'dev-secret-key', 'dev-secret-change-in-production'])
def test_production_refuses_missing_or_placeholder_key(key):
    with pytest.raises(RuntimeError):
        token_signing_issue(key, production=True)


def test_development_only_warns_on_placeholder():
    assert token_signing_issue('dev-secret-key', production=False) == 'placeholder'


def test_short_key_warns_but_starts():
    assert token_signing_issue('x' * 20, production=True) == 'short'
    assert '32' in SIGNING_ISSUE_MESSAGES['short']


def test_good_key_is_quiet():
    assert token_signing_issue('a' * 64, production=True) is None


def test_create_app_production_refuses_placeholder(monkeypatch):
    from app import create_app
    from config import ProductionConfig
    monkeypatch.setattr(ProductionConfig, 'SECRET_KEY', 'dev-secret-key')
    with pytest.raises(RuntimeError):
        create_app('production')
