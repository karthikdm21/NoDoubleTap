"""Exception-based simulator tests, never process-kill or real network evidence."""

import json

import pytest

from agent_transaction_lab.before_execution import (
    BeforeExecutionTimeoutTransport,
    SimulatedBeforeExecutionTimeout,
    main,
    recover_before_execution_timeout,
    run_demo,
)
from agent_transaction_lab.models import RefundRequest
from agent_transaction_lab.service import ServiceError, seeded_service


def request():
    return RefundRequest(
        intent_id="intent_demo_001",
        payment_id="payment_demo_001",
        amount_minor=2500,
        currency="INR",
        approval_id="approval_demo_001",
    )


def test_fault_occurs_before_execution_and_original_key_replay_has_one_effect():
    service = seeded_service()
    transport = BeforeExecutionTimeoutTransport(service)
    with pytest.raises(SimulatedBeforeExecutionTimeout):
        transport.refund("stable_key", request())
    assert service.ledger().effects == ()
    with pytest.raises(ServiceError) as error:
        service.operation("stable_key")
    assert error.value.status_code == 404
    result = transport.refund("stable_key", request())
    assert transport.refund("stable_key", request()) == result
    assert len(service.ledger().effects) == 1


def test_recovery_queries_status_then_retries_exact_key_and_request():
    class RecordingTransport(BeforeExecutionTimeoutTransport):
        def __init__(self, service):
            super().__init__(service)
            self.calls = []

        def refund(self, key, payload):
            self.calls.append(("refund", key, payload))
            return super().refund(key, payload)

        def operation(self, key):
            self.calls.append(("status", key))
            return super().operation(key)

    service = seeded_service()
    transport = RecordingTransport(service)
    payload = request()
    result = recover_before_execution_timeout(transport, "stable_key", payload)
    assert transport.calls == [
        ("refund", "stable_key", payload),
        ("status", "stable_key"),
        ("refund", "stable_key", payload),
    ]
    assert result.outcome == "committed"
    assert [event.sequence for event in result.evidence] == list(range(1, 8))
    assert [event.kind for event in result.evidence] == [
        "refund_attempt",
        "response_lost",
        "status_query",
        "status_not_found",
        "refund_attempt",
        "refund_acknowledged",
        "final_outcome",
    ]
    assert [event.outcome for event in result.evidence[:5]] == ["unknown"] * 5
    assert [event.attempt for event in result.evidence] == [1, 1, 1, 1, 2, 2, 2]
    assert (transport.refund_attempts, transport.status_queries) == (2, 1)
    assert service.ledger().refunded_minor == 2500


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("private_marker"),
        ServiceError(503, "private_marker"),
        ServiceError(403, "private_marker"),
    ],
)
def test_unavailable_status_stays_unknown_without_a_retry(failure):
    class UnavailableStatus(BeforeExecutionTimeoutTransport):
        def operation(self, key):
            self.status_queries += 1
            raise failure

    service = seeded_service()
    transport = UnavailableStatus(service)
    result = recover_before_execution_timeout(transport, "stable_key", request())
    assert result.outcome == "unknown" and result.operation is None
    assert result.evidence[-2].kind == "status_unresolved"
    assert (transport.refund_attempts, transport.status_queries) == (1, 1)
    assert service.ledger().effects == ()
    assert "private_marker" not in result.model_dump_json()


@pytest.mark.parametrize("corruption", ["key", "payload", "effect_key", "hash"])
def test_mismatched_status_never_authorizes_another_dispatch(corruption):
    class WrongStatus(BeforeExecutionTimeoutTransport):
        def operation(self, key):
            result = super().operation(key)
            if corruption == "key":
                return result.model_copy(update={"operation_key": "wrong"})
            if corruption == "hash":
                return result.model_copy(update={"payload_hash": "wrong"})
            effect = (
                result.effect.model_copy(update={"operation_key": "wrong"})
                if corruption == "effect_key"
                else result.effect.model_copy(
                    update={"request": request().model_copy(update={"amount_minor": 3000})}
                )
            )
            return result.model_copy(update={"effect": effect})

    service = seeded_service()
    service.refund("stable_key", request())
    transport = WrongStatus(service)
    result = recover_before_execution_timeout(transport, "stable_key", request())
    assert result.outcome == "unknown" and result.operation is None
    assert result.evidence[-2].kind == "status_mismatch"
    assert (transport.refund_attempts, transport.status_queries) == (1, 1)
    assert len(service.ledger().effects) == 1


def test_existing_committed_status_reconciles_without_a_second_send():
    service = seeded_service()
    previous = service.refund("stable_key", request())
    transport = BeforeExecutionTimeoutTransport(service)
    result = recover_before_execution_timeout(transport, "stable_key", request())
    assert result.operation == previous and result.outcome == "committed"
    assert transport.refund_attempts == 1
    assert len(service.ledger().effects) == 1


def test_a_404_does_not_prove_no_effect_but_same_key_retry_is_safe():
    class HiddenStatus(BeforeExecutionTimeoutTransport):
        def operation(self, key):
            self.status_queries += 1
            raise ServiceError(404, "not visible")

    service = seeded_service()
    previous = service.refund("stable_key", request())
    transport = HiddenStatus(service)
    result = recover_before_execution_timeout(transport, "stable_key", request())
    assert result.operation == previous
    assert (transport.refund_attempts, transport.status_queries) == (2, 1)
    assert len(service.ledger().effects) == 1


def test_retry_response_lost_after_commit_ends_unknown_at_the_call_budget():
    class LostRetryResponse(BeforeExecutionTimeoutTransport):
        def refund(self, key, payload):
            super().refund(key, payload)
            raise TimeoutError("private_marker")

    service = seeded_service()
    transport = LostRetryResponse(service)
    result = recover_before_execution_timeout(transport, "stable_key", request())
    assert result.outcome == "unknown" and result.operation is None
    assert result.evidence[-2].kind == "retry_exhausted"
    assert result.evidence[-1].attempt == 2
    assert (transport.refund_attempts, transport.status_queries) == (2, 1)
    assert len(service.ledger().effects) == 1
    assert "private_marker" not in result.model_dump_json()


def test_service_denial_on_retry_is_not_converted_to_success():
    service = seeded_service()
    transport = BeforeExecutionTimeoutTransport(service)
    denied = request().model_copy(update={"approval_id": "denied"})
    with pytest.raises(ServiceError) as error:
        recover_before_execution_timeout(transport, "stable_key", denied)
    assert error.value.status_code == 403
    assert service.ledger().effects == ()
    assert (transport.refund_attempts, transport.status_queries) == (2, 1)


def test_fixed_demo_is_repeatable_and_exports_safe_ledger_evidence(capsys):
    report = run_demo()
    assert report == run_demo()
    assert report.scenario_id == "timeout_before_execution_v1"
    assert report.outcome == "committed" and report.ledger_assertions_passed
    assert (report.refund_attempts, report.status_queries, report.ledger_effect_count) == (2, 1, 1)
    assert (report.refunded_minor, report.remaining_minor) == (2500, 7500)
    main()
    assert json.loads(capsys.readouterr().out) == json.loads(report.model_dump_json())
    assert "approval_demo_001" not in report.model_dump_json()


def test_retry_acknowledgement_mismatch_is_not_reported_as_success():
    class WrongAcknowledgement(BeforeExecutionTimeoutTransport):
        def refund(self, key, payload):
            result = super().refund(key, payload)
            return result.model_copy(update={"operation_key": "wrong_key"})

    service = seeded_service()
    transport = WrongAcknowledgement(service)
    result = recover_before_execution_timeout(transport, "stable_key", request())
    assert result.outcome == "unknown" and result.operation is None
    assert result.evidence[-2].kind == "status_mismatch"
    assert (transport.refund_attempts, transport.status_queries) == (2, 1)
    assert len(service.ledger().effects) == 1
