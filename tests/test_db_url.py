import pytest
from sqlalchemy.engine import make_url

from config import _normalize_db_url


@pytest.mark.parametrize('url,expected', [
    ('postgres://u:p@db:5432/j3d', 'postgresql+psycopg2://u:p@db:5432/j3d'),
    ('postgresql://u:p@db:5432/j3d', 'postgresql+psycopg2://u:p@db:5432/j3d'),
    ('postgresql+psycopg2://u:p@db/j3d', 'postgresql+psycopg2://u:p@db/j3d'),
    ('postgresql+psycopg://u:p@db/j3d', 'postgresql+psycopg://u:p@db/j3d'),   # an explicit driver is respected
    ('sqlite:///j3d.db', 'sqlite:///j3d.db'),
    (None, None),
    ('', ''),
])
def test_normalize_db_url(url, expected):
    assert _normalize_db_url(url) == expected


def test_postgres_urls_use_the_installed_driver():
    """Guards against SQLAlchemy picking a driver that isn't installed (2.1 defaults to psycopg v3)."""
    dialect = make_url(_normalize_db_url('postgresql://u:p@db/j3d')).get_dialect()
    assert dialect.driver == 'psycopg2'
    dialect.import_dbapi()   # psycopg2-binary must be importable


def test_safe_db_url_masks_the_password():
    from config import safe_db_url
    shown = safe_db_url('postgresql+psycopg2://j3d_user:Sup3r$ecret&@db:5432/j3d')
    assert 'Sup3r' not in shown
    assert shown == 'postgresql+psycopg2://j3d_user:***@db:5432/j3d'
    assert safe_db_url('sqlite:///j3d.db') == 'sqlite:///j3d.db'
    assert safe_db_url(None) == 'None'
