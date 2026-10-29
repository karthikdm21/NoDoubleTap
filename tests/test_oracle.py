"""Independent fixtures deliberately include inconsistent storage snapshots."""

import hashlib
import json

import pytest

from agent_transaction_lab.models import (
    Approval,
    FakePayment,
    LedgerSnapshot,
    Operation,
    RefundEffect,
    RefundRequest,
)
from agent_transaction_lab.oracle import evaluate_ledger
from agent_transaction_lab.service import seeded_service


def request(**changes):
    return RefundRequest(
        **{
            "intent_id": "intent_demo_001",
            "payment_id": "payment_demo_001",
            "amount_minor": 2500,
            "currency": "INR",
            "approval_id": "approval_demo_001",
            **changes,
        }
    )


def snapshot(requests, keys=None):
    keys = keys or [f"key_{i}" for i in range(len(requests))]
    effects = tuple(
        RefundEffect(effect_id=f"effect_{i}", operation_key=keys[i], request=r)
        for i, r in enumerate(requests)
    )
    operations = tuple(
        Operation(
            operation_key=e.operation_key,
            effect=e,
            payload_hash=hashlib.sha256(
                json.dumps(e.request.model_dump(), sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        for e in effects
    )
    total = sum(r.amount_minor for r in requests)
    ledger = LedgerSnapshot(
        payment=FakePayment(
            payment_id="payment_demo_001", order_id="order_demo_001", captured_minor=10000
        ),
        refunded_minor=total,
        remaining_minor=10000 - total,
        effects=effects,
    )
    return ledger, tuple(Approval(**r.model_dump()) for r in requests), operations


def test_real_seeded_service_snapshot_is_consistent_without_changing_it():
    service = seeded_service()
    op = service.refund("original_key", request())
    before = service.ledger()
    report = evaluate_ledger(before, (Approval(**request().model_dump()),), (op,))
    assert report.consistent and report.effect_total_minor == 2500
    assert report.effect_count == report.operation_count == 1
    assert service.ledger() == before


def test_empty_ledger_integrity_is_not_proof_of_requested_completion():
    ledger, approvals, operations = snapshot([])
    report = evaluate_ledger(ledger, approvals, operations)
    assert report.consistent and report.effect_count == 0
    assert "outcome" not in report.model_dump()


@pytest.mark.parametrize(
    "change",
    [
        {"amount_minor": 2600},
        {"approval_id": "not_approved"},
        {"intent_id": "other_intent"},
        {"payment_id": "other_payment"},
    ],
)
def test_exact_approval_binding_detects_changed_effect(change):
    ledger, _, operations = snapshot([request(**change)])
    report = evaluate_ledger(ledger, (Approval(**request().model_dump()),), operations)
    assert "unauthorized_effect" in report.violations


def test_an_approved_effect_cannot_belong_to_a_different_payment_scope():
    ledger, approvals, operations = snapshot([request(payment_id="other_payment")])
    assert "effect_outside_payment" in evaluate_ledger(ledger, approvals, operations).violations


def test_duplicate_intent_across_distinct_keys_is_detected():
    ledger, approvals, operations = snapshot([request(), request()])
    report = evaluate_ledger(ledger, (approvals[0],), operations)
    assert "duplicate_logical_refund" in report.violations
    assert "duplicate_operation_key" not in report.violations


def test_repeated_ledger_row_is_not_hidden_by_set_comparison():
    ledger, approvals, operations = snapshot([request()])
    ledger = ledger.model_copy(
        update={
            "effects": (ledger.effects[0],) * 2,
            "refunded_minor": 5000,
            "remaining_minor": 5000,
        }
    )
    report = evaluate_ledger(ledger, approvals, operations)
    assert {
        "duplicate_effect_id",
        "duplicate_operation_key",
        "duplicate_logical_refund",
        "operation_ledger_mismatch",
    }.issubset(report.violations)


def test_actual_effect_sum_exposes_over_refund_even_when_reported_balance_looks_safe():
    ledger, approvals, operations = snapshot(
        [
            request(amount_minor=6000),
            request(amount_minor=6000, intent_id="intent_2", approval_id="approval_2"),
        ]
    )
    ledger = ledger.model_copy(update={"refunded_minor": 6000, "remaining_minor": 4000})
    report = evaluate_ledger(ledger, approvals, operations)
    assert report.effect_total_minor == 12000
    assert {"capture_exceeded", "refunded_total_mismatch", "remaining_total_mismatch"}.issubset(
        report.violations
    )


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "wrong_effect", "wrong_key"])
def test_committed_operation_must_match_actual_ledger_exactly(mutation):
    ledger, approvals, operations = snapshot([request()])
    op = operations[0]
    changed = {
        "missing": (),
        "extra": (op, op.model_copy(update={"operation_key": "other_key"})),
        "duplicate": (op, op),
        "wrong_effect": (
            op.model_copy(
                update={"effect": op.effect.model_copy(update={"effect_id": "other_effect"})}
            ),
        ),
        "wrong_key": (op.model_copy(update={"operation_key": "other_key"}),),
    }[mutation]
    report = evaluate_ledger(ledger, approvals, changed)
    assert not report.consistent and "operation_ledger_mismatch" in report.violations


def test_hash_and_duplicate_approval_evidence_is_safe_and_deterministic():
    ledger, approvals, operations = snapshot([request()])
    operations = (operations[0].model_copy(update={"payload_hash": "invalid"}),)
    report = evaluate_ledger(ledger, approvals * 2, operations)
    assert {"duplicate_approval_id", "operation_hash_mismatch"}.issubset(report.violations)
    assert report == evaluate_ledger(ledger, approvals * 2, operations)
    exported = report.model_dump_json()
    assert "approval_demo_001" not in exported and "payment_demo_001" not in exported
    assert set(report.model_dump()) == {
        "violations",
        "effect_count",
        "operation_count",
        "effect_total_minor",
    }


def test_inconsistent_negative_reported_balances_are_detected():
    ledger, approvals, operations = snapshot([])
    ledger = ledger.model_copy(update={"refunded_minor": -1, "remaining_minor": 10001})
    assert "negative_reported_balance" in evaluate_ledger(ledger, approvals, operations).violations
