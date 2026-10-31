"""Deterministic before-execution timeout; bounded same-key scripted recovery."""

import hashlib
import json
from typing import Literal

from agent_transaction_lab.models import Approval, LedgerSnapshot, Operation, RefundRequest
from agent_transaction_lab.oracle import evaluate_ledger
from agent_transaction_lab.repository import TransactionRepository
from agent_transaction_lab.scenario import Reconciliation, ScenarioEvent, ScenarioReport
from agent_transaction_lab.service import ServiceError, seeded_service


class SimulatedBeforeExecutionTimeout(TimeoutError):
    """No result reached the caller. Only the harness knows dispatch was suppressed."""


class BeforeExecutionTimeoutTransport:
    """Suppress the first refund before touching the service; fresh instance per run."""

    def __init__(self, service: TransactionRepository) -> None:
        self.service = service
        self.refund_attempts = 0
        self.status_queries = 0

    def refund(self, key: str, request: RefundRequest) -> Operation:
        self.refund_attempts += 1
        if self.refund_attempts == 1:
            raise SimulatedBeforeExecutionTimeout("Simulated timeout before service execution")
        return self.service.refund(key, request)

    def operation(self, key: str) -> Operation:
        self.status_queries += 1
        return self.service.operation(key)

    def ledger(self) -> LedgerSnapshot:
        return self.service.ledger()


def recover_before_execution_timeout(
    transport: BeforeExecutionTimeoutTransport, key: str, request: RefundRequest
) -> Reconciliation:
    """At most two sends and one query. A 404 permits only the original-key retry.

    This is an in-memory scripted policy, not persisted identity or crash recovery.
    A failed/mismatched status does not justify dispatch under a replacement key.
    """
    evidence = [ScenarioEvent(sequence=1, kind="refund_attempt")]
    payload = json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()

    def matches(op: Operation) -> bool:
        return (
            op.operation_key == key
            and op.effect.operation_key == key
            and op.effect.request == request
            and op.payload_hash == digest
        )

    def finish(
        op: Operation | None,
        kind: Literal[
            "status_unresolved",
            "status_mismatch",
            "status_reconciled",
            "refund_acknowledged",
            "retry_exhausted",
        ],
        attempt: int = 1,
    ) -> Reconciliation:
        outcome: Literal["unknown", "committed"] = "committed" if op is not None else "unknown"
        evidence.append(
            ScenarioEvent(sequence=len(evidence) + 1, kind=kind, attempt=attempt, outcome=outcome)
        )
        evidence.append(
            ScenarioEvent(
                sequence=len(evidence) + 1, kind="final_outcome", attempt=attempt, outcome=outcome
            )
        )
        return Reconciliation(
            outcome=outcome,
            operation=op,
            events=tuple(event.kind for event in evidence),
            evidence=tuple(evidence),
        )

    try:
        result = transport.refund(key, request)
    except SimulatedBeforeExecutionTimeout:
        evidence.append(ScenarioEvent(sequence=2, kind="response_lost"))
        evidence.append(ScenarioEvent(sequence=3, kind="status_query"))
        try:
            result = transport.operation(key)
        except TimeoutError:
            return finish(None, "status_unresolved")
        except ServiceError as error:
            if error.status_code != 404:
                return finish(None, "status_unresolved")
            # Not-found is still uncertain; never manufacture a new operation key.
            evidence.append(ScenarioEvent(sequence=4, kind="status_not_found"))
        else:
            return finish(
                result if matches(result) else None,
                "status_reconciled" if matches(result) else "status_mismatch",
            )
        evidence.append(ScenarioEvent(sequence=5, kind="refund_attempt", attempt=2))
        try:
            result = transport.refund(key, request)
        except TimeoutError:
            evidence.append(ScenarioEvent(sequence=6, kind="response_lost", attempt=2))
            return finish(None, "retry_exhausted", attempt=2)
        # Service rejections propagate; they must never become committed success.
        return finish(
            result if matches(result) else None,
            "refund_acknowledged" if matches(result) else "status_mismatch",
            attempt=2,
        )
    return finish(
        result if matches(result) else None,
        "refund_acknowledged" if matches(result) else "status_mismatch",
    )


def run_demo() -> ScenarioReport:
    service = seeded_service()
    transport = BeforeExecutionTimeoutTransport(service)
    request = RefundRequest(
        intent_id="intent_demo_001",
        payment_id="payment_demo_001",
        amount_minor=2500,
        currency="INR",
        approval_id="approval_demo_001",
    )
    result = recover_before_execution_timeout(transport, "op_demo_001", request)
    ledger = service.ledger()
    # Inspector reads stored operations directly, not the controller's returned result.
    operations = tuple(service.operation(effect.operation_key) for effect in ledger.effects)
    integrity = evaluate_ledger(ledger, (Approval(**request.model_dump()),), operations)
    passed = (
        integrity.consistent
        and len(ledger.effects) == 1
        and ledger.refunded_minor == 2500
        and ledger.remaining_minor == 7500
        and ledger.effects[0].operation_key == "op_demo_001"
    )
    return ScenarioReport(
        scenario_id="timeout_before_execution_v1",
        outcome=result.outcome,
        evidence=result.evidence,
        refund_attempts=transport.refund_attempts,
        status_queries=transport.status_queries,
        ledger_effect_count=len(ledger.effects),
        refunded_minor=ledger.refunded_minor,
        remaining_minor=ledger.remaining_minor,
        ledger_assertions_passed=passed,
    )


def main() -> None:
    report = run_demo()
    print(report.model_dump_json(indent=2))
    if not report.ledger_assertions_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
