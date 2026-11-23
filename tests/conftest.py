import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-pytest-0123456789abcdef')
os.environ.setdefault('ETSY_CLIENT_ID', 'test-client-id')
os.environ.setdefault('ETSY_CLIENT_SECRET', 'test-client-secret')

import pytest
from app import create_app
from models import db, User


@pytest.fixture
def app():
    application = create_app('testing')
    application.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def user(app):
    u = User(etsy_user_id='123', username='tester', access_token='tok', shop_id='shop1')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def token(user):
    from authentication import TokenManager
    return TokenManager.create_token(user.id)
