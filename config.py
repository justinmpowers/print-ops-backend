import os
from datetime import timedelta


def _normalize_db_url(url: str | None) -> str | None:
    """Ensure a SQLAlchemy-friendly Postgres scheme that uses the installed driver.

    SQLAlchemy 2.1 changed the default driver for plain ``postgresql://`` URLs from
    psycopg2 to psycopg (v3). Only psycopg2 is installed, so name it explicitly; a URL
    that already names a driver (``postgresql+...://``) is left alone.
    """
    if not url:
        return url
    for prefix in ('postgres://', 'postgresql://'):
        if url.startswith(prefix):
            return 'postgresql+psycopg2://' + url[len(prefix):]
    return url


def safe_db_url(url: str | None) -> str:
    """The database URL with any password masked, for logging."""
    if not url:
        return str(url)
    try:
        from sqlalchemy.engine import make_url
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return '<unparseable database URL>'


class Config:
    """Base configuration"""
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(os.getenv('DATABASE_URL', 'sqlite:///j3d.db'))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    AUTO_DB_CREATE = True  # dev/test convenience; disabled in production
    
    # Etsy API Configuration
    ETSY_CLIENT_ID = os.getenv('ETSY_CLIENT_ID')
    ETSY_CLIENT_SECRET = os.getenv('ETSY_CLIENT_SECRET')
    ETSY_REDIRECT_URI = os.getenv('ETSY_REDIRECT_URI', 'http://localhost:4200/oauth-callback')
    ETSY_API_BASE_URL = 'https://openapi.etsy.com/v3'
    
    # CORS Configuration
    CORS_ORIGINS = ['http://localhost:4200', 'http://localhost:3000']
    
    # HTTP client configuration
    HTTP_TIMEOUT = float(os.getenv('HTTP_TIMEOUT', '10'))
    
    # JWT / session configuration
    ACCESS_TOKEN_EXPIRATION_MINUTES = int(os.getenv('ACCESS_TOKEN_EXPIRATION_MINUTES', '30'))
    REFRESH_TOKEN_EXPIRATION_DAYS = int(os.getenv('REFRESH_TOKEN_EXPIRATION_DAYS', '14'))

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False
    SQLALCHEMY_DATABASE_URI = _normalize_db_url(
        os.getenv('DATABASE_URL', 'postgresql://localhost/j3d')
    )
    AUTO_DB_CREATE = False

class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///j3d_test.db'

config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
