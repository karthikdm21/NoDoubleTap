"""Offline/configuration checks; these do not execute PostgreSQL SQL."""

from io import StringIO
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError

from agent_transaction_lab.models import FakePayment, RefundRequest
from agent_transaction_lab.postgres import local_engine, payload_hash, seed_run, transaction
from agent_transaction_lab.service import ServiceError, seeded_service


def config():
    output = StringIO()
    return Config("alembic.ini", output_buffer=output), output


def test_upgrade_generates_postgres_ddl_without_connection_configuration(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cfg, output = config()
    command.upgrade(cfg, "head", sql=True)
    sql = output.getvalue()
    assert "BEGIN;" in sql and "COMMIT;" in sql
    assert "CREATE TABLE public.ndt_effects" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "CREATE TRIGGER ndt_intents_immutable" in sql
    assert "23514" in sql


def test_downgrade_generates_only_owned_objects_without_cascade():
    cfg, output = config()
    command.downgrade(cfg, "0001_transactions:base", sql=True)
    sql = output.getvalue()
    assert "DROP TABLE public.ndt_effects" in sql
    assert "CASCADE" not in sql
    assert "DROP DATABASE" not in sql


def test_online_migration_requires_separate_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("NODOUBLETAP_ALLOW_MIGRATIONS", raising=False)
    cfg, _ = config()
    with pytest.raises(RuntimeError, match="explicit dedicated-database opt-in"):
        command.upgrade(cfg, "head")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite://",
        "postgresql+psycopg://localhost/other_database",
        "postgresql+psycopg://remote.example/nodoubletap_sim",
        "postgresql+psycopg://localhost:5433/nodoubletap_sim",
        "postgresql+psycopg://localhost/nodoubletap_sim?host=remote.example",
    ],
)
def test_wrong_connection_targets_are_refused_without_connecting(url):
    with pytest.raises(ValueError) as error:
        local_engine(url)
    assert url not in str(error.value) or not url


def test_valid_engine_construction_is_lazy():
    engine = local_engine("postgresql+psycopg://localhost:5432/nodoubletap_sim")
    assert engine.dialect.name == "postgresql"
    engine.dispose()


def test_commit_failure_remains_unknown_and_sanitized():
    engine = MagicMock()
    engine.begin.return_value.__exit__.side_effect = SQLAlchemyError("synthetic-private-detail")
    with pytest.raises(ServiceError) as error:
        with transaction(engine):
            pass
    assert error.value.status_code == 503
    assert "unknown" in error.value.detail
    assert "synthetic-private-detail" not in str(error.value)


def request():
    return RefundRequest(
        intent_id="intent_demo_001",
        payment_id="payment_demo_001",
        approval_id="approval_demo_001",
        amount_minor=2500,
        currency="INR",
    )


def test_hash_preserves_existing_service_contract():
    expected = seeded_service().refund("original_key", request()).payload_hash
    assert payload_hash(request()) == expected
    assert payload_hash(request().model_copy(update={"amount_minor": 2600})) != expected


def test_unapproved_seed_is_rejected_before_database_access():
    from uuid import uuid4

    engine = MagicMock()
    engine.dialect.name = "postgresql"
    payment = FakePayment(
        payment_id="payment_demo_001", order_id="order_demo_001", captured_minor=10000
    )
    with pytest.raises(ValueError, match="exact approved"):
        seed_run(engine, uuid4(), payment, (), {"original_key": request()})
    engine.begin.assert_not_called()
