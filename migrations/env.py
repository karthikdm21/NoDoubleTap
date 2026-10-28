"""Offline SQL is safe by default. Online execution needs explicit local opt-in."""

import os

from alembic import context

from agent_transaction_lab.postgres import local_engine

if context.is_offline_mode():
    context.configure(dialect_name="postgresql", literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    if os.environ.get("NODOUBLETAP_ALLOW_MIGRATIONS") != "1":
        raise RuntimeError("Online migrations require explicit dedicated-database opt-in")
    engine = local_engine(os.environ.get("DATABASE_URL", ""))
    try:
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
