import pytest

from agent_transaction_lab.models import RefundRequest
from agent_transaction_lab.scenario import (
    ResponseLossTransport,
    SimulatedResponseLost,
    reconcile_refund,
)
from agent_transaction_lab.service import ServiceError, seeded_service


def request():
    return RefundRequest(
        intent_id="intent_demo_001",
        payment_id="payment_demo_001",
        amount_minor=2500,
        currency="INR",
        approval_id="approval_demo_001",
    )


def test_response_is_lost_only_after_effect_exists_and_replay_is_safe():
    service = seeded_service()
    transport = ResponseLossTransport(service)
    with pytest.raises(SimulatedResponseLost):
        transport.refund("stable_key", request())
    assert service.ledger().refunded_minor == 2500
    status = transport.operation("stable_key")
    assert transport.refund("stable_key", request()) == status
    assert len(service.ledger().effects) == 1


def test_controller_reconciles_same_key_without_second_dispatch():
    service = seeded_service()
    transport = ResponseLossTransport(service)
    result = reconcile_refund(transport, "stable_key", request())
    assert result.outcome == "committed"
    assert result.operation == service.operation("stable_key")
    assert result.events == (
        "refund_dispatched",
        "response_lost_outcome_unknown",
        "status_reconciled",
    )
    assert transport.refund_attempts == transport.status_queries == 1
    ledger = service.ledger()
    assert len(ledger.effects) == 1
    assert ledger.effects[0].operation_key == "stable_key"
    assert (ledger.refunded_minor, ledger.remaining_minor) == (2500, 7500)


@pytest.mark.parametrize("failure", [TimeoutError(), ServiceError(404, "not found")])
def test_failed_status_query_keeps_unknown_without_retry(failure):
    class UnavailableStatus(ResponseLossTransport):
        def operation(self, key):
            self.status_queries += 1
            raise failure

    service = seeded_service()
    transport = UnavailableStatus(service)
    result = reconcile_refund(transport, "stable_key", request())
    assert result.outcome == "unknown"
    assert result.operation is None
    assert result.events[-1] == "status_unresolved"
    assert transport.refund_attempts == transport.status_queries == 1
    assert len(service.ledger().effects) == 1


def test_wrong_status_is_not_reported_as_success():
    class WrongStatus(ResponseLossTransport):
        def operation(self, key):
            return self.service.operation(key).model_copy(update={"operation_key": "wrong"})

    result = reconcile_refund(WrongStatus(seeded_service()), "stable_key", request())
    assert result.outcome == "unknown"
    assert result.events[-1] == "status_mismatch"


def test_denial_is_not_hidden_as_response_loss():
    service = seeded_service()
    transport = ResponseLossTransport(service)
    denied = request().model_copy(update={"approval_id": "denied"})
    with pytest.raises(ServiceError) as exc:
        reconcile_refund(transport, "denied_key", denied)
    assert exc.value.status_code == 403
    assert transport.status_queries == 0
    assert service.ledger().effects == ()
    with pytest.raises(SimulatedResponseLost):
        transport.refund("valid_key", request())


def test_each_new_transport_reproduces_failure():
    for _ in range(2):
        transport = ResponseLossTransport(seeded_service())
        with pytest.raises(SimulatedResponseLost):
            transport.refund("stable_key", request())


def test_structured_report_is_deterministic_and_has_ordered_safe_evidence(capsys):
    import json

    from agent_transaction_lab.scenario import main, run_demo

    first = run_demo()
    assert first == run_demo()
    assert [event.sequence for event in first.evidence] == [1, 2, 3, 4, 5]
    assert [event.kind for event in first.evidence] == [
        "refund_attempt",
        "response_lost",
        "status_query",
        "status_reconciled",
        "final_outcome",
    ]
    assert [event.outcome for event in first.evidence] == [
        "unknown",
        "unknown",
        "unknown",
        "committed",
        "committed",
    ]
    assert first.ledger_effect_count == 1
    assert (first.refunded_minor, first.remaining_minor) == (2500, 7500)
    assert first.refund_attempts == first.status_queries == 1
    assert first.ledger_assertions_passed
    main()
    exported = json.loads(capsys.readouterr().out)
    assert exported == json.loads(first.model_dump_json())
    assert set(exported) == {
        "schema_version",
        "scenario_id",
        "outcome",
        "evidence",
        "refund_attempts",
        "status_queries",
        "ledger_effect_count",
        "refunded_minor",
        "remaining_minor",
        "ledger_assertions_passed",
    }
    assert all(
        set(event) == {"sequence", "kind", "attempt", "outcome"} for event in exported["evidence"]
    )


@pytest.mark.parametrize("mode", ["timeout", "missing", "wrong_key", "wrong_payload"])
def test_unknown_evidence_never_claims_commit_or_leaks_error(mode):
    class FaultyStatus(ResponseLossTransport):
        def operation(self, key):
            self.status_queries += 1
            if mode == "timeout":
                raise TimeoutError("secret-error-text")
            if mode == "missing":
                raise ServiceError(404, "secret-error-text")
            result = self.service.operation(key)
            if mode == "wrong_key":
                return result.model_copy(update={"operation_key": "wrong"})
            changed = request().model_copy(update={"approval_id": "secret-error-text"})
            return result.model_copy(
                update={"effect": result.effect.model_copy(update={"request": changed})}
            )

    service = seeded_service()
    transport = FaultyStatus(service)
    result = reconcile_refund(transport, "stable_key", request())
    assert result.outcome == "unknown"
    assert result.operation is None
    assert [event.sequence for event in result.evidence] == [1, 2, 3, 4, 5]
    assert [event.kind for event in result.evidence[:3]] == [
        "refund_attempt",
        "response_lost",
        "status_query",
    ]
    assert result.evidence[-1].kind == "final_outcome"
    assert all(event.outcome == "unknown" for event in result.evidence)
    assert "secret-error-text" not in result.model_dump_json()
    assert transport.refund_attempts == transport.status_queries == 1
    assert len(service.ledger().effects) == 1
