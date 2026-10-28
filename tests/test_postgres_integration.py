"""Opt-in real PostgreSQL tests. Never substitute SQLite or auto-create a database."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from agent_transaction_lab.models import Approval, FakePayment, RefundRequest
from agent_transaction_lab.postgres import PostgresTransactionRepository, local_engine, seed_run
from agent_transaction_lab.service import ServiceError


@pytest.fixture
def database():
    url = os.environ.get("NODOUBLETAP_TEST_DATABASE_URL")
    if os.environ.get("NODOUBLETAP_ALLOW_DB_TESTS") != "1" or not url:
        pytest.skip("Dedicated PostgreSQL connection and explicit DB-test opt-in required")
    engine = local_engine(url)
    run = uuid4()
    payment = FakePayment(payment_id="payment_test", order_id="order_test", captured_minor=10000)
    requests = tuple(
        RefundRequest(
            intent_id=f"intent_{i}",
            payment_id="payment_test",
            amount_minor=amount,
            currency="INR",
            approval_id=f"approval_{i}",
        )
        for i, amount in enumerate((2500, 6000, 6000))
    )
    approvals = tuple(Approval(**r.model_dump()) for r in requests)
    intents = {f"key_{i}": r for i, r in enumerate(requests)}
    seed_run(engine, run, payment, approvals, intents)
    try:
        yield engine, run, payment, approvals, intents, requests
    finally:
        # Append-only fixtures remain under unique synthetic run IDs. No broad cleanup.
        engine.dispose()


def oracle(engine, run):
    # Independent connection and raw ledger query, not repository status or agent output.
    with engine.connect() as connection:
        count, total = connection.execute(
            text("""
            SELECT count(*), coalesce(sum(amount_minor), 0)
            FROM public.ndt_effects WHERE run_id = :run
        """),
            {"run": run},
        ).one()
        balance = connection.execute(
            text("""
            SELECT refunded_minor FROM public.ndt_payments WHERE run_id = :run
        """),
            {"run": run},
        ).scalar_one()
    return count, total, balance


def test_same_key_concurrency_and_reopen(database):
    engine, run, payment, approvals, intents, requests = database
    repository = PostgresTransactionRepository(engine, run, payment.payment_id)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repository.refund("key_0", requests[0]), range(8)))
    assert all(result == results[0] for result in results)
    assert oracle(engine, run) == (1, 2500, 2500)
    # Dispose connections and read through a new repository, not a process-kill test.
    engine.dispose()
    assert (
        PostgresTransactionRepository(engine, run, payment.payment_id).operation("key_0")
        == results[0]
    )
    seed_run(engine, run, payment, approvals, intents)
    assert oracle(engine, run) == (1, 2500, 2500)


def test_competing_refunds_cannot_exceed_capture(database):
    engine, run, payment, _, _, requests = database
    repository = PostgresTransactionRepository(engine, run, payment.payment_id)

    def attempt(i):
        try:
            repository.refund(f"key_{i}", requests[i])
            return 200
        except ServiceError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, (1, 2))) == [200, 409]
    assert oracle(engine, run) == (1, 6000, 6000)


def test_conflicting_key_payload_and_seed_do_not_add_effect(database):
    engine, run, payment, approvals, intents, requests = database
    repository = PostgresTransactionRepository(engine, run, payment.payment_id)
    repository.refund("key_0", requests[0])
    for key, request in (
        ("new_key", requests[0]),
        ("key_0", requests[0].model_copy(update={"amount_minor": 2600})),
    ):
        with pytest.raises(ServiceError) as error:
            repository.refund(key, request)
        assert error.value.status_code == 409
    with pytest.raises(ServiceError):
        seed_run(
            engine, run, payment.model_copy(update={"captured_minor": 11000}), approvals, intents
        )
    assert oracle(engine, run) == (1, 2500, 2500)


def test_database_constraint_failure_rolls_back_balance(database):
    engine, run, _, _, _, _ = database
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE public.ndt_payments SET refunded_minor=2500 WHERE run_id=:run"),
                {"run": run},
            )
            # This operation has neither an approved intent nor a ledger effect.
            connection.execute(
                text("""
                INSERT INTO public.ndt_operations
                    (run_id, operation_key, intent_id, payload_hash, status)
                VALUES (:run, 'invalid_key', 'absent_intent', 'invalid_hash', 'committed')
            """),
                {"run": run},
            )
    assert oracle(engine, run) == (0, 0, 0)
