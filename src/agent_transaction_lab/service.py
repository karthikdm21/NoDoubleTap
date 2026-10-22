"""Small in-memory service. No durability or cross-process guarantees."""

import hashlib
import json
from threading import Lock

from agent_transaction_lab.models import (
    Approval,
    FakePayment,
    LedgerSnapshot,
    Operation,
    RefundEffect,
    RefundRequest,
)


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FakeTransactionService:
    """One payment per instance; callers cannot grant themselves approvals."""

    def __init__(self, payment: FakePayment, approvals: tuple[Approval, ...]) -> None:
        self._payment = payment
        self._approvals = {approval.approval_id: approval for approval in approvals}
        self._operations: dict[str, Operation] = {}
        self._intents: set[str] = set()
        self._effects: list[RefundEffect] = []
        self._lock = Lock()

    def refund(self, key: str, request: RefundRequest) -> Operation:
        payload = json.dumps(request.model_dump(), sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()
        with self._lock:
            previous = self._operations.get(key)
            if previous is not None:
                if previous.payload_hash != payload_hash:
                    raise ServiceError(409, "Operation key already has a different payload")
                return previous
            if request.intent_id in self._intents:
                raise ServiceError(409, "Intent already committed under another operation key")
            if request.payment_id != self._payment.payment_id:
                raise ServiceError(404, "Fake payment not found")
            approval = self._approvals.get(request.approval_id)
            if approval is None or approval.model_dump() != request.model_dump():
                raise ServiceError(403, "No matching seeded test approval")
            total = sum(effect.request.amount_minor for effect in self._effects)
            if total + request.amount_minor > self._payment.captured_minor:
                raise ServiceError(409, "Refund would exceed captured amount")
            effect = RefundEffect(
                effect_id=f"refund_{len(self._effects) + 1:03d}",
                operation_key=key,
                request=request,
            )
            operation = Operation(operation_key=key, payload_hash=payload_hash, effect=effect)
            # This lock serializes threads only. These writes are NOT a database transaction.
            self._effects.append(effect)
            self._operations[key] = operation
            self._intents.add(request.intent_id)
            return operation

    def operation(self, key: str) -> Operation:
        with self._lock:
            if key not in self._operations:
                raise ServiceError(404, "Operation not found in this process")
            return self._operations[key]

    def ledger(self) -> LedgerSnapshot:
        with self._lock:
            total = sum(effect.request.amount_minor for effect in self._effects)
            return LedgerSnapshot(
                payment=self._payment,
                refunded_minor=total,
                remaining_minor=self._payment.captured_minor - total,
                effects=tuple(self._effects),
            )


def seeded_service() -> FakeTransactionService:
    return FakeTransactionService(
        payment=FakePayment(
            order_id="order_demo_001", payment_id="payment_demo_001", captured_minor=10000
        ),
        approvals=(
            Approval(
                intent_id="intent_demo_001",
                payment_id="payment_demo_001",
                amount_minor=2500,
                currency="INR",
                approval_id="approval_demo_001",
            ),
        ),
    )
