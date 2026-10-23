"""The SQL migrations and the SQLAlchemy models must describe the same schema."""

from __future__ import annotations

from sqlalchemy import inspect

from novara.db import Base
from tests.conftest import requires_pg

pytestmark = requires_pg


def test_migrations_match_models(pg_owner_engine):
    live = inspect(pg_owner_engine)
    live_tables = set(live.get_table_names()) - {"schema_migrations"}
    assert live_tables == set(Base.metadata.tables), "tables differ between migrations and models"
    problems = []
    for name, table in Base.metadata.tables.items():
        live_cols = {c["name"]: c for c in live.get_columns(name)}
        model_cols = {c.name: c for c in table.columns}
        if set(live_cols) != set(model_cols):
            problems.append(f"{name}: columns {sorted(set(live_cols) ^ set(model_cols))}")
            continue
        for col_name, col in model_cols.items():
            if live_cols[col_name]["nullable"] != col.nullable:
                problems.append(f"{name}.{col_name}: nullable differs")
            expected = col.type.compile(dialect=pg_owner_engine.dialect)
            actual = live_cols[col_name]["type"].compile(dialect=pg_owner_engine.dialect)
            if expected != actual:
                problems.append(f"{name}.{col_name}: {actual} in DB vs {expected} in model")
    assert not problems, "\n".join(problems)
