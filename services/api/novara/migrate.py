"""Apply SQL migrations in order and record them in ``schema_migrations``.

Run as the schema-owner role, never as the API's runtime role:

    python -m novara.migrate --database-url postgresql+psycopg://owner@host/novara --app-role novara_app
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import create_engine, text

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations" / "postgres"


def migrate(database_url: str, app_role: str | None = None, directory: Path = MIGRATIONS_DIR) -> list[str]:
    engine = create_engine(database_url)
    applied_now: list[str] = []
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        ))
    for path in sorted(directory.glob("*.sql")):
        version = path.stem
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(4242)"))
            if conn.scalar(text("SELECT 1 FROM schema_migrations WHERE version = :v"), {"v": version}):
                continue
            if app_role:
                conn.execute(text("SELECT set_config('novara.app_role', :r, true)"), {"r": app_role})
            # Raw DBAPI cursor with no parameters, so '%' in SQL (e.g. format('%I')) isn't treated as a placeholder.
            conn.connection.cursor().execute(path.read_text(encoding="utf-8"))
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": version})
            applied_now.append(version)
    engine.dispose()
    return applied_now


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--app-role", help="runtime role to grant privileges to")
    args = parser.parse_args()
    for version in migrate(args.database_url, args.app_role):
        print(f"applied {version}")


if __name__ == "__main__":
    main()
