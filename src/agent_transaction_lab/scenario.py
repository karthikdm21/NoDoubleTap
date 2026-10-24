"""Deterministic in-process response loss and bounded status reconciliation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from agent_transaction_lab.models import Operation, RefundRequest
from agent_transaction_lab.service import FakeTransactionService, ServiceError, seeded_service


class SimulatedResponseLost(TimeoutError):
    """The caller received no result. It must not infer the ledger outcome."""


class ResponseLossTransport:
    """Hide the first successful refund response per instance; never alter the ledger."""

    def __init__(self, service: FakeTransactionService) -> None:
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


class Reconciliation(BaseModel):
    model_config = ConfigDict(frozen=True)
    outcome: Literal["committed", "unknown"]
    operation: Operation | None
    events: tuple[str, ...]


def reconcile_refund(
    transport: ResponseLossTransport, key: str, request: RefundRequest
) -> Reconciliation:
    """Send once, query once after response loss. Never create a replacement key."""
    try:
        result = transport.refund(key, request)
    except SimulatedResponseLost:
        events = ("refund_dispatched", "response_lost_outcome_unknown")
        try:
            result = transport.operation(key)
        except (TimeoutError, ServiceError):
            return Reconciliation(
                outcome="unknown", operation=None, events=(*events, "status_unresolved")
            )
        # Bind the status result to this exact intent, not merely a success label.
        if result.operation_key != key or result.effect.request != request:
            return Reconciliation(
                outcome="unknown", operation=None, events=(*events, "status_mismatch")
            )
        return Reconciliation(
            outcome="committed", operation=result, events=(*events, "status_reconciled")
        )
    return Reconciliation(outcome="committed", operation=result, events=("refund_acknowledged",))


def main() -> None:
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
    print(result.model_dump_json(indent=2))
    print(ledger.model_dump_json(indent=2))
    print(f"refund_attempts={transport.refund_attempts} status_queries={transport.status_queries}")
    print(f"ledger_assertions={'PASS' if passed else 'FAIL'}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
