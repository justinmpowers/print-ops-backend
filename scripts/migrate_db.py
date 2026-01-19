import os
import sys
import argparse
from pathlib import Path

# Add parent directory to path to import app
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import _normalize_db_url
from app import create_app
from models import db
from flask_migrate import init as migrate_init, migrate as migrate_migrate, upgrade as migrate_upgrade
from sqlalchemy import text


def _resolve_migrations_dir() -> Path:
    """Return a writable migrations directory.

    Priority:
    1) MIGRATIONS_DIR env var if set
    2) repo-level migrations directory
    3) /tmp/migrations as a last resort
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


def _clear_alembic_version():
    """Clear stale alembic_version table if it references missing revisions."""
    try:
        with db.engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            print("✓ Cleared stale alembic_version table")
    except Exception as e:
        print(f"Note: Could not clear alembic_version: {e}")


def _reinitialize_database(app_config):
    """Clear stale alembic state and recreate database using create_all."""
    print("Detected stale migration state. Clearing alembic_version and using create_all...")
    _clear_alembic_version()
    db.create_all()
    print(f"✓ Tables created directly at {app_config['SQLALCHEMY_DATABASE_URI']}")


def main():
    parser = argparse.ArgumentParser(description="Generate and apply database migrations for J3D backend")
    parser.add_argument("--config", default="development", help="App config name (development, production, testing)")
    parser.add_argument("--url", dest="url", default=os.getenv("DATABASE_URL"), help="Database URL; defaults to env DATABASE_URL")
    parser.add_argument("--message", "-m", default="Auto-generated migration", help="Migration message")
    parser.add_argument("--apply", action="store_true", help="Apply migrations after generating")
    parser.add_argument("--force-recreate", action="store_true", help="Drop and recreate all tables (bypasses migrations)")
    args = parser.parse_args()

    db_url = _normalize_db_url(args.url) if args.url else None
    if db_url:
        os.environ["DATABASE_URL"] = db_url

    app = create_app(args.config)
    
    migrations_dir = _resolve_migrations_dir()
    
    with app.app_context():
        # Force recreate mode: drop everything and use create_all
        if args.force_recreate:
            print("Force recreate mode: dropping all tables...")
            db.drop_all()
            print("Creating all tables...")
            db.create_all()
            print(f"✓ Database recreated at {app.config['SQLALCHEMY_DATABASE_URI']}")
            return 0

        env_py = migrations_dir / "env.py"

        # Initialize migrations if directory missing or env.py missing
        if (not migrations_dir.exists()) or (not env_py.exists()):
            print("Initializing migrations directory...")
            try:
                # Clear any stale alembic_version that might cause conflicts
                _clear_alembic_version()
                
                # Remove partial directory if it exists but is missing env.py to avoid alembic confusion
                if migrations_dir.exists() and not env_py.exists():
                    # keep directory but re-init to generate env.py and script versions
                    migrate_init(directory=str(migrations_dir))
                else:
                    migrate_init(directory=str(migrations_dir))
                print(f"✓ Migrations initialized at {migrations_dir}")
            except Exception as e:
                print(f"✗ Migration init failed: {e}")
                return 1
        
        # Generate migration
        print(f"Generating migration: {args.message}")
        try:
            migrate_migrate(directory=str(migrations_dir), message=args.message)
            print(f"✓ Migration generated successfully")
        except Exception as e:
            error_msg = str(e)
            
            # Check if it's a missing revision error
            if "Can't locate revision" in error_msg:
                print(f"✗ Migration generation failed: {error_msg}")
                _reinitialize_database(app.config)
                return 0
            
            # For other errors, provide context
            print(f"✗ Migration generation failed: {error_msg}")
            # Only show "normal" message for actual no-changes scenarios
            if "No changes in schema detected" in error_msg.lower():
                print("  (This is normal if no schema changes detected)")
        
        
        # Apply migrations if requested
        if args.apply:
            print("Applying migrations...")
            try:
                migrate_upgrade(directory=str(migrations_dir))
                print(f"✓ Migrations applied to {app.config['SQLALCHEMY_DATABASE_URI']}")
            except Exception as e:
                error_msg = str(e)
                print(f"✗ Migration upgrade failed: {error_msg}")
                
                # Check if it's a missing revision error
                if "Can't locate revision" in error_msg:
                    _reinitialize_database(app.config)
                    return 0
                
                return 1
    
    print(f"\n✓ Database config={args.config} at {app.config['SQLALCHEMY_DATABASE_URI']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
