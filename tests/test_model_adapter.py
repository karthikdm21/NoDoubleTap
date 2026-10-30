"""Scripted model-output contracts; no network, paid inference or persistence."""

import json

import pytest

from agent_transaction_lab.model_adapter import (
    MAX_OUTPUT_BYTES,
    InvalidModelOutput,
    ModelAdapter,
    ModelCallBudgetExhausted,
    ModelFixtureExhausted,
    RefundToolCall,
    ScriptedModelAdapter,
    StatusToolCall,
    dispatch_tool_call,
    parse_tool_call,
)
from agent_transaction_lab.service import ServiceError, seeded_service


def refund_output(**changes):
    payload = {
        "tool": "refund",
        "operation_key": "original_key",
        "arguments": {
            "intent_id": "intent_demo_001",
            "payment_id": "payment_demo_001",
            "amount_minor": 2500,
            "currency": "INR",
            "approval_id": "approval_demo_001",
        },
    }
    payload.update(changes)
    return json.dumps(payload)


def test_valid_scripted_refund_and_status_use_the_exact_key_and_actual_ledger():
    adapter: ModelAdapter = ScriptedModelAdapter(
        (refund_output(), json.dumps({"tool": "operation_status", "operation_key": "original_key"}))
    )
    service = seeded_service()
    call = adapter.next_call()
    assert isinstance(call, RefundToolCall)
    assert service.ledger().effects == ()  # A model decision alone has no effect.
    result = dispatch_tool_call(call, service)
    status = adapter.next_call()
    assert isinstance(status, StatusToolCall)
    assert status.operation_key == result.operation_key == "original_key"
    assert dispatch_tool_call(status, service) == result
    assert len(service.ledger().effects) == 1
    assert service.ledger().refunded_minor == 2500


@pytest.mark.parametrize(
    "output",
    [
        "not JSON",
        "[]",
        "null",
        '{"tool":"shell","operation_key":"original_key","arguments":{}}',
        '{"tool":"operation_status","operation_key":"original_key","extra":"private_marker"}',
        '{"tool":"refund","arguments":{}}',
        '{"tool":"operation_status","operation_key":" "}',
        '{"tool":"refund","operation_key":"original_key","arguments":{"amount_minor":"2500"}}',
    ],
)
def test_bad_model_output_cannot_reach_a_tool_or_change_the_ledger(output, capsys):
    service = seeded_service()
    adapter = ScriptedModelAdapter((output,))
    with pytest.raises(InvalidModelOutput) as error:
        dispatch_tool_call(adapter.next_call(), service)
    assert adapter.calls_used == 1
    assert "private_marker" not in str(error.value)
    assert service.ledger().effects == ()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("amount", ["2500", 2500.0, True, 0, -1])
def test_model_refund_money_is_strict_and_positive(amount):
    data = json.loads(refund_output())
    data["arguments"]["amount_minor"] = amount
    with pytest.raises(InvalidModelOutput):
        parse_tool_call(json.dumps(data))


def test_invalid_output_consumes_budget_and_cannot_trigger_an_extra_dispatch():
    adapter = ScriptedModelAdapter(("invalid", refund_output(), refund_output()), max_calls=2)
    service = seeded_service()
    with pytest.raises(InvalidModelOutput):
        adapter.next_call()
    dispatch_tool_call(adapter.next_call(), service)
    with pytest.raises(ModelCallBudgetExhausted):
        dispatch_tool_call(adapter.next_call(), service)
    assert adapter.calls_used == 2
    assert len(service.ledger().effects) == 1


def test_missing_fixture_is_explicit_and_also_consumes_its_finite_budget():
    adapter = ScriptedModelAdapter((), max_calls=1)
    with pytest.raises(ModelFixtureExhausted):
        adapter.next_call()
    assert adapter.calls_used == 1
    with pytest.raises(ModelCallBudgetExhausted):
        adapter.next_call()


@pytest.mark.parametrize("limit", [0, -1, 33, True, "2", 2.0])
def test_budget_configuration_does_not_coerce_or_allow_unbounded_values(limit):
    with pytest.raises(ValueError, match="max_calls"):
        ScriptedModelAdapter((), max_calls=limit)


def test_output_size_and_invalid_unicode_fail_with_no_raw_details():
    for output in ("private_marker" + "x" * MAX_OUTPUT_BYTES, "\ud800"):
        with pytest.raises(InvalidModelOutput) as error:
            parse_tool_call(output)
        assert "private_marker" not in str(error.value)


def test_new_model_key_for_existing_intent_is_rejected_by_the_service():
    service = seeded_service()
    adapter = ScriptedModelAdapter(
        (refund_output(), refund_output(operation_key="replacement_key"))
    )
    dispatch_tool_call(adapter.next_call(), service)
    with pytest.raises(ServiceError) as error:
        dispatch_tool_call(adapter.next_call(), service)
    assert error.value.status_code == 409
    assert len(service.ledger().effects) == 1


def test_missing_status_is_not_success_and_does_not_create_an_effect():
    service = seeded_service()
    call = parse_tool_call('{"tool":"operation_status","operation_key":"missing"}')
    with pytest.raises(ServiceError) as error:
        dispatch_tool_call(call, service)
    assert error.value.status_code == 404
    assert service.ledger().effects == ()


def test_new_instances_replay_identical_immutable_decisions_without_shared_budget():
    first = ScriptedModelAdapter((refund_output(),))
    second = ScriptedModelAdapter((refund_output(),))
    call = first.next_call()
    assert second.calls_used == 0
    assert call == second.next_call()
    with pytest.raises(ValueError):
        call.operation_key = "changed_key"


@pytest.mark.parametrize("change", [{"approval_id": "denied"}, {"amount_minor": 3000}])
def test_valid_model_schema_does_not_override_service_approval(change):
    data = json.loads(refund_output())
    data["arguments"].update(change)
    adapter = ScriptedModelAdapter((json.dumps(data),))
    service = seeded_service()
    with pytest.raises(ServiceError) as error:
        dispatch_tool_call(adapter.next_call(), service)
    assert error.value.status_code == 403
    assert service.ledger().effects == ()
