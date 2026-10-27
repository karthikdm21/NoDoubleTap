"""Deterministic in-process response loss and bounded status reconciliation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from agent_transaction_lab.models import Operation, RefundRequest
from agent_transaction_lab.repository import TransactionRepository
from agent_transaction_lab.service import ServiceError, seeded_service


class SimulatedResponseLost(TimeoutError):
    """The caller received no result. It must not infer the ledger outcome."""


class ResponseLossTransport:
    """Hide the first successful refund response per instance; never alter the ledger."""

    def __init__(self, service: TransactionRepository) -> None:
        self.service = service
        self._lost = False
        self.refund_attempts = 0
        self.status_queries = 0

    def refund(self, key: str, request: RefundRequest) -> Operation:
        self.refund_attempts += 1
        result = self.service.refund(key, request)
        if not self._lost:
            self._lost = True
            raise SimulatedResponseLost("Simulated response lost after service commit")
        return result

    def operation(self, key: str) -> Operation:
        self.status_queries += 1
        return self.service.operation(key)


class ScenarioEvent(BaseModel):
    """Allowlisted evidence: no request bodies, exception text or credentials."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    sequence: int
    kind: Literal[
        "refund_attempt",
        "response_lost",
        "status_query",
        "status_unresolved",
        "status_mismatch",
        "status_reconciled",
        "refund_acknowledged",
        "final_outcome",
    ]
    attempt: int = 1
    outcome: Literal["unknown", "committed"] = "unknown"


class Reconciliation(BaseModel):
    model_config = ConfigDict(frozen=True)
    outcome: Literal["committed", "unknown"]
    operation: Operation | None
    events: tuple[str, ...]
    evidence: tuple[ScenarioEvent, ...] = ()


def reconcile_refund(
    transport: ResponseLossTransport, key: str, request: RefundRequest
) -> Reconciliation:
    """Send once, query once after response loss. Never create a replacement key."""
    evidence = [ScenarioEvent(sequence=1, kind="refund_attempt")]

    def finish(
        outcome: Literal["committed", "unknown"],
        operation: Operation | None,
        events: tuple[str, ...],
        kind: Literal[
            "status_unresolved", "status_mismatch", "status_reconciled", "refund_acknowledged"
        ],
    ) -> Reconciliation:
        evidence.append(ScenarioEvent(sequence=len(evidence) + 1, kind=kind, outcome=outcome))
        evidence.append(
            ScenarioEvent(sequence=len(evidence) + 1, kind="final_outcome", outcome=outcome)
        )
        return Reconciliation(
            outcome=outcome, operation=operation, events=events, evidence=tuple(evidence)
        )

    try:
        result = transport.refund(key, request)
    except SimulatedResponseLost:
        events = ("refund_dispatched", "response_lost_outcome_unknown")
        evidence.append(ScenarioEvent(sequence=2, kind="response_lost"))
        evidence.append(ScenarioEvent(sequence=3, kind="status_query"))
        try:
            result = transport.operation(key)
        except (TimeoutError, ServiceError):
            return finish("unknown", None, (*events, "status_unresolved"), "status_unresolved")
        # Bind the status result to this exact intent, not merely a success label.
        if result.operation_key != key or result.effect.request != request:
            return finish("unknown", None, (*events, "status_mismatch"), "status_mismatch")
        return finish("committed", result, (*events, "status_reconciled"), "status_reconciled")
    return finish("committed", result, ("refund_acknowledged",), "refund_acknowledged")


class ScenarioReport(BaseModel):
    """Versioned evidence for one fixed demo; not a durable run store."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    scenario_id: Literal["lost_response_after_commit_v1"] = "lost_response_after_commit_v1"
    outcome: Literal["committed", "unknown"]
    evidence: tuple[ScenarioEvent, ...]
    refund_attempts: int
    status_queries: int
    ledger_effect_count: int
    refunded_minor: int
    remaining_minor: int
    ledger_assertions_passed: bool


def run_demo() -> ScenarioReport:
    """Print deterministic evidence; use the ledger as the independent oracle."""
    service = seeded_service()
    transport = ResponseLossTransport(service)
    request = RefundRequest(
        intent_id="intent_demo_001",
        payment_id="payment_demo_001",
        amount_minor=2500,
        currency="INR",
        approval_id="approval_demo_001",
    )
    result = reconcile_refund(transport, "op_demo_001", request)
    ledger = service.ledger()
    passed = (
        result.outcome == "committed"
        and len(ledger.effects) == 1
        and ledger.refunded_minor == 2500
        and ledger.remaining_minor == 7500
        and ledger.effects[0].operation_key == "op_demo_001"
    )
    return ScenarioReport(
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
