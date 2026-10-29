"""Read-only single-payment integrity checks, independent of service decisions."""

import hashlib
import json
from collections import Counter
from typing import Literal

from agent_transaction_lab.models import Approval, Contract, LedgerSnapshot, Operation

Violation = Literal[
    "duplicate_approval_id",
    "unauthorized_effect",
    "effect_outside_payment",
    "duplicate_effect_id",
    "duplicate_operation_key",
    "duplicate_logical_refund",
    "refunded_total_mismatch",
    "remaining_total_mismatch",
    "capture_exceeded",
    "negative_reported_balance",
    "duplicate_operation_record",
    "operation_ledger_mismatch",
    "operation_hash_mismatch",
]


class LedgerIntegrityReport(Contract):
    """Safe aggregate evidence; consistency alone is not scenario completion."""

    violations: tuple[Violation, ...]
    effect_count: int
    operation_count: int
    effect_total_minor: int

    @property
    def consistent(self) -> bool:
        return not self.violations


def evaluate_ledger(
    ledger: LedgerSnapshot, approvals: tuple[Approval, ...], operations: tuple[Operation, ...]
) -> LedgerIntegrityReport:
    """Evaluate a complete, coherent snapshot; never query or mutate the service.

    Callers must obtain approvals, effects and operations from authoritative storage
    in one consistent scope/snapshot. Model messages and trace events are not inputs.
    Empty consistent state can still mean a requested action was never completed.
    """
    violations: list[Violation] = []

    def flag(condition: bool, code: Violation) -> None:
        if condition and code not in violations:
            violations.append(code)

    effects = ledger.effects
    flag(len({a.approval_id for a in approvals}) != len(approvals), "duplicate_approval_id")
    flag(
        any(not any(a.model_dump() == e.request.model_dump() for a in approvals) for e in effects),
        "unauthorized_effect",
    )
    flag(
        any(
            e.request.payment_id != ledger.payment.payment_id
            or e.request.currency != ledger.payment.currency
            for e in effects
        ),
        "effect_outside_payment",
    )
    flag(len({e.effect_id for e in effects}) != len(effects), "duplicate_effect_id")
    flag(len({e.operation_key for e in effects}) != len(effects), "duplicate_operation_key")
    flag(len({e.request.intent_id for e in effects}) != len(effects), "duplicate_logical_refund")
    total = sum(e.request.amount_minor for e in effects)
    flag(ledger.refunded_minor != total, "refunded_total_mismatch")
    flag(
        ledger.remaining_minor != ledger.payment.captured_minor - total, "remaining_total_mismatch"
    )
    flag(total > ledger.payment.captured_minor, "capture_exceeded")
    flag(ledger.refunded_minor < 0 or ledger.remaining_minor < 0, "negative_reported_balance")
    counts = Counter(op.operation_key for op in operations)
    flag(any(n != 1 for n in counts.values()), "duplicate_operation_record")
    # Exact effect equality checks the key, intended action and actual effect ID.
    # Compare multisets too: a missing or repeated operation is not committed truth.
    ledger_keys = Counter(e.operation_key for e in effects)
    flag(
        counts != ledger_keys
        or any(
            op.effect.operation_key != op.operation_key or op.effect not in effects
            for op in operations
        ),
        "operation_ledger_mismatch",
    )
    for op in operations:
        # Independently implement the declared canonical payload contract. Do not
        # call a service hash/check function to validate that same service.
        payload = json.dumps(op.effect.request.model_dump(), sort_keys=True, separators=(",", ":"))
        flag(
            op.payload_hash != hashlib.sha256(payload.encode()).hexdigest(),
            "operation_hash_mismatch",
        )
    return LedgerIntegrityReport(
        violations=tuple(violations),
        effect_count=len(effects),
        operation_count=len(operations),
        effect_total_minor=total,
    )
