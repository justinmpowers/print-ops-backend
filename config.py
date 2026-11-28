import os


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
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')   # production refuses this default (see token_signing_issue)
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
    # Sessions started with "Keep me signed in" end this many days after they were last used.
    REMEMBER_ME_REFRESH_TOKEN_DAYS = int(os.getenv('REMEMBER_ME_REFRESH_TOKEN_DAYS', '90'))

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

# Placeholder secrets from this repo's config, compose file and examples. Anyone can read them,
# so a server signing login tokens with one of them lets anyone forge a token for any user.
KNOWN_PLACEHOLDER_SECRETS = {
    'dev-secret-key',
    'dev-secret-change-in-production',
    'your-super-secret-key-change-this-in-production',
    'generate_random_key',
}
MIN_SECRET_KEY_LENGTH = 32   # HS256 wants a key at least as long as its 256-bit output


# What to log for each token-signing setting problem. Fixed text only: nothing derived from the
# setting's value is ever logged.
SIGNING_ISSUE_MESSAGES = {
    'placeholder': 'SECRET_KEY is a placeholder; fine for development, never for production',
    'short': 'SECRET_KEY is shorter than 32 characters; use a longer random value (openssl rand -hex 32)',
}


def token_signing_issue(value, production):
    """Classify the token-signing setting: None if fine, 'placeholder' or 'short' otherwise.

    Raises RuntimeError in production for a missing or placeholder value, since anyone could then
    forge login tokens. Returns only an issue code, so callers log SIGNING_ISSUE_MESSAGES[code].
    """
    if not value or value in KNOWN_PLACEHOLDER_SECRETS:
        if production:
            raise RuntimeError(
                'SECRET_KEY is unset or a placeholder value, so anyone could forge login tokens. '
                'Set it to a long random value, e.g. the output of: openssl rand -hex 32')
        return 'placeholder'
    if len(value) < MIN_SECRET_KEY_LENGTH:
        return 'short'
    return None


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
