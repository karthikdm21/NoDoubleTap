"""Candidate PostgreSQL adapter; database integration acceptance is still pending."""

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, RowMapping, make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from agent_transaction_lab.models import (
    Approval,
    FakePayment,
    Identifier,
    LedgerSnapshot,
    Operation,
    RefundEffect,
    RefundRequest,
)
from agent_transaction_lab.service import ServiceError


def payload_hash(request: RefundRequest) -> str:
    payload = json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def validate_local_url(url: str | URL) -> URL:
    """Validate the exact project target without connecting or exposing credentials."""
    try:
        parsed = make_url(url)
        allowed = (
            parsed.drivername == "postgresql+psycopg"
            and parsed.host == "127.0.0.1"
            and parsed.database == "nodoubletap_sim"
            and parsed.port == 5433
            and not parsed.query
        )
    except Exception:
        raise ValueError("Invalid local simulation database configuration") from None
    if not allowed:
        raise ValueError("Only the dedicated local nodoubletap_sim database is supported")
    return parsed


def local_engine(url: str | URL) -> Engine:
    """Construct lazily after validating the isolated project target."""
    return create_engine(
        validate_local_url(url),
        echo=False,
        hide_parameters=True,
        connect_args={"connect_timeout": 3},
    )


@contextmanager
def transaction(engine: Engine) -> Iterator[Connection]:
    """No retries or success on ambiguous commit; suppress driver/URL details."""
    try:
        with engine.begin() as connection:
            yield connection
    except IntegrityError:
        raise ServiceError(409, "Simulation transaction constraint conflict") from None
    except SQLAlchemyError:
        raise ServiceError(503, "Simulation store unavailable; outcome may be unknown") from None


def _request(row: RowMapping) -> RefundRequest:
    return RefundRequest(**{name: row[name] for name in RefundRequest.model_fields})


def _operation(connection: Connection, run: UUID, key: str) -> Operation | None:
    row = (
        connection.execute(
            text("""
        SELECT o.operation_key, o.payload_hash, e.effect_id, e.intent_id,
               e.payment_id, e.amount_minor, e.currency, e.approval_id
        FROM public.ndt_operations o JOIN public.ndt_effects e
          ON e.run_id = o.run_id AND e.operation_key = o.operation_key
        WHERE o.run_id = :run AND o.operation_key = :key
    """),
            {"run": run, "key": key},
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return Operation(
        operation_key=key,
        payload_hash=row["payload_hash"],
        effect=RefundEffect(effect_id=row["effect_id"], operation_key=key, request=_request(row)),
    )


class PostgresTransactionRepository:
    """One explicit run and payment; no automatic seed, migration or memory fallback."""

    def __init__(self, engine: Engine, run_id: UUID, payment_id: str) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("PostgreSQL is required; SQLite cannot prove these constraints")
        self.engine = engine
        self.run_id = run_id
        self.payment_id = TypeAdapter(Identifier).validate_python(payment_id)

    def refund(self, key: str, request: RefundRequest) -> Operation:
        key = TypeAdapter(Identifier).validate_python(key)
        digest = payload_hash(request)
        with transaction(self.engine) as connection:
            intent = (
                connection.execute(
                    text("""
                SELECT * FROM public.ndt_intents
                WHERE run_id = :run AND intent_id = :intent FOR UPDATE
            """),
                    {"run": self.run_id, "intent": request.intent_id},
                )
                .mappings()
                .first()
            )
            if intent is None:
                raise ServiceError(403, "No pre-persisted approved simulation intent")
            if intent["operation_key"] != key or intent["payload_hash"] != digest:
                raise ServiceError(409, "Intent/key payload conflict")
            if _request(intent) != request or request.payment_id != self.payment_id:
                raise ServiceError(403, "No matching simulation approval in this scope")
            payment = connection.execute(
                text("""
                SELECT payment_id FROM public.ndt_payments
                WHERE run_id = :run AND payment_id = :payment FOR UPDATE
            """),
                {"run": self.run_id, "payment": self.payment_id},
            ).first()
            if payment is None:
                raise ServiceError(404, "Fake payment not found")
            result = _operation(connection, self.run_id, key)
            if result is None:
                updated = connection.execute(
                    text("""
                    UPDATE public.ndt_payments SET refunded_minor = refunded_minor + :amount
                    WHERE run_id = :run AND payment_id = :payment
                      AND refunded_minor + :amount <= captured_minor
                    RETURNING payment_id
                """),
                    {
                        "run": self.run_id,
                        "payment": self.payment_id,
                        "amount": request.amount_minor,
                    },
                ).first()
                if updated is None:
                    raise ServiceError(409, "Refund would exceed captured amount")
                values = {
                    "run": self.run_id,
                    "key": key,
                    "hash": digest,
                    "effect": "refund_" + uuid4().hex,
                    **request.model_dump(),
                }
                connection.execute(
                    text("""
                    INSERT INTO public.ndt_operations
                        (run_id, operation_key, intent_id, payload_hash, status)
                    VALUES (:run, :key, :intent_id, :hash, 'committed')
                """),
                    values,
                )
                connection.execute(
                    text("""
                    INSERT INTO public.ndt_effects
                      (run_id, effect_id, operation_key, intent_id, payment_id,
                       amount_minor, currency, approval_id)
                    VALUES (:run, :effect, :key, :intent_id, :payment_id,
                            :amount_minor, :currency, :approval_id)
                """),
                    values,
                )
                result = _operation(connection, self.run_id, key)
                if result is None:
                    raise RuntimeError("Inserted operation has no ledger effect")
        # Do not return from inside the transaction: commit must finish first.
        return result

    def operation(self, key: str) -> Operation:
        with transaction(self.engine) as connection:
            result = _operation(connection, self.run_id, key)
            if result is None or result.effect.request.payment_id != self.payment_id:
                raise ServiceError(404, "Operation not found in this simulation scope")
        return result

    def ledger(self) -> LedgerSnapshot:
        with transaction(self.engine) as connection:
            # Lock against concurrent refunds so totals and effects share one snapshot.
            payment = (
                connection.execute(
                    text("""
                SELECT * FROM public.ndt_payments
                WHERE run_id = :run AND payment_id = :payment FOR SHARE
            """),
                    {"run": self.run_id, "payment": self.payment_id},
                )
                .mappings()
                .first()
            )
            if payment is None:
                raise ServiceError(404, "Fake payment not found")
            rows = (
                connection.execute(
                    text("""
                SELECT * FROM public.ndt_effects
                WHERE run_id = :run AND payment_id = :payment ORDER BY effect_id
            """),
                    {"run": self.run_id, "payment": self.payment_id},
                )
                .mappings()
                .all()
            )
            snapshot = LedgerSnapshot(
                payment=FakePayment(**{name: payment[name] for name in FakePayment.model_fields}),
                refunded_minor=payment["refunded_minor"],
                remaining_minor=payment["captured_minor"] - payment["refunded_minor"],
                effects=tuple(
                    RefundEffect(
                        effect_id=row["effect_id"],
                        operation_key=row["operation_key"],
                        request=_request(row),
                    )
                    for row in rows
                ),
            )
        return snapshot


def seed_run(
    engine: Engine,
    run_id: UUID,
    payment: FakePayment,
    approvals: tuple[Approval, ...],
    intents: Mapping[str, RefundRequest],
) -> None:
    """Explicit fixture seeding only. Refuse conflicting seed; never erase existing state."""
    if engine.dialect.name != "postgresql":
        raise ValueError("PostgreSQL is required")
    for key, request in intents.items():
        TypeAdapter(Identifier).validate_python(key)
        if request.payment_id != payment.payment_id or not any(
            approval.model_dump() == request.model_dump() for approval in approvals
        ):
            raise ValueError("Seed intent needs its exact approved fake payment payload")
    data = {
        "payment": payment.model_dump(),
        "approvals": sorted((a.model_dump() for a in approvals), key=lambda a: a["approval_id"]),
        "intents": {k: v.model_dump() for k, v in sorted(intents.items())},
    }
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with transaction(engine) as connection:
        inserted = connection.execute(
            text("""
            INSERT INTO public.ndt_runs (run_id, seed_digest) VALUES (:run, :digest)
            ON CONFLICT (run_id) DO NOTHING RETURNING run_id
        """),
            {"run": run_id, "digest": digest},
        ).first()
        if inserted is None:
            existing = connection.execute(
                text("SELECT seed_digest FROM public.ndt_runs WHERE run_id = :run"), {"run": run_id}
            ).scalar_one()
            if existing != digest:
                raise ServiceError(409, "Simulation seed conflicts with existing run")
            return
        connection.execute(
            text("""
            INSERT INTO public.ndt_payments
              (run_id, payment_id, order_id, currency, captured_minor)
            VALUES (:run, :payment_id, :order_id, :currency, :captured_minor)
        """),
            {"run": run_id, **payment.model_dump()},
        )
        for approval in approvals:
            connection.execute(
                text("""
                INSERT INTO public.ndt_approvals
                    (run_id, approval_id, intent_id, payment_id, amount_minor, currency)
                VALUES (:run, :approval_id, :intent_id, :payment_id, :amount_minor, :currency)
            """),
                {"run": run_id, **approval.model_dump()},
            )
        for key, request in intents.items():
            connection.execute(
                text("""
                INSERT INTO public.ndt_intents
                    (run_id, operation_key, intent_id, payment_id, amount_minor,
                     currency, approval_id, payload_hash)
                VALUES (:run, :key, :intent_id, :payment_id, :amount_minor,
                        :currency, :approval_id, :hash)
            """),
                {"run": run_id, "key": key, "hash": payload_hash(request), **request.model_dump()},
            )
