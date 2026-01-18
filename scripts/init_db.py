import os
import sys
import argparse
from pathlib import Path

# Add parent directory to path to import app
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import _normalize_db_url
from app import create_app
from models import db
from flask_migrate import upgrade as migrate_upgrade


def _resolve_migrations_dir() -> Path:
    """Return a writable migrations directory.

    Checks MIGRATIONS_DIR env, repo migrations, then /tmp/migrations.
    """
    candidates = []
    env_dir = os.getenv("MIGRATIONS_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.append(Path(__file__).parent.parent / "migrations")
    candidates.append(Path("/tmp/migrations"))

    for path in candidates:
        if path.exists():
            if os.access(path, os.W_OK):
                return path
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
            if os.access(path, os.W_OK):
                return path
        except PermissionError:
            continue
    raise PermissionError("Unable to create a writable migrations directory; checked MIGRATIONS_DIR, repo migrations, and /tmp/migrations")


def main():
    parser = argparse.ArgumentParser(description="Initialize database tables for J3D backend")
    parser.add_argument("--config", default="development", help="App config name (development, production, testing)")
    parser.add_argument("--url", dest="url", default=os.getenv("DATABASE_URL"), help="Database URL; defaults to env DATABASE_URL or sqlite for dev")
    parser.add_argument("--migrate", action="store_true", help="Use migrations instead of create_all (recommended for production)")
    args = parser.parse_args()

    db_url = _normalize_db_url(args.url) if args.url else None
    if db_url:
        os.environ["DATABASE_URL"] = db_url

    app = create_app(args.config)
    migrations_dir = _resolve_migrations_dir()
    
    with app.app_context():
        if args.migrate:
            print("Applying migrations...")
            try:
                migrate_upgrade(directory=str(migrations_dir))
                print(f"✓ Migrations applied")
            except Exception as e:
                print(f"✗ Migration upgrade failed: {e}")
                return
        else:
            db.create_all()
            print(f"✓ Created tables via create_all()")
        
        print(f"Database ready: config={args.config} at {app.config['SQLALCHEMY_DATABASE_URI']}")


if __name__ == "__main__":
    main()
