from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from agent_transaction_lab.main import create_app
from agent_transaction_lab.models import Approval, FakePayment, RefundRequest
from agent_transaction_lab.service import FakeTransactionService, ServiceError, seeded_service


def payload(**changes):
    return {
        "intent_id": "intent_demo_001",
        "payment_id": "payment_demo_001",
        "amount_minor": 2500,
        "currency": "INR",
        "approval_id": "approval_demo_001",
        **changes,
    }


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def send(client, key="op_demo_001", **changes):
    return client.post("/sim/refunds", headers={"Idempotency-Key": key}, json=payload(**changes))


def test_refund_and_replay_have_one_actual_effect(client):
    first = send(client)
    second = send(client)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    ledger = client.get("/sim/ledger").json()
    assert len(ledger["effects"]) == 1
    assert ledger["refunded_minor"] == 2500
    assert ledger["remaining_minor"] == 7500
    assert client.get("/sim/operations/op_demo_001").json() == first.json()


def test_conflicting_key_and_duplicate_intent_do_not_add_effects(client):
    send(client)
    assert send(client, amount_minor=3000).status_code == 409
    assert send(client, key="new_key").status_code == 409
    assert len(client.get("/sim/ledger").json()["effects"]) == 1


@pytest.mark.parametrize("amount", [0, -1, 2.5, 2500.0, "2500", True, None])
def test_invalid_amounts_have_no_effect(client, amount):
    assert send(client, amount_minor=amount).status_code == 422
    assert client.get("/sim/ledger").json()["effects"] == []


@pytest.mark.parametrize("change", [{"currency": "USD"}, {"intent_id": " "}, {"extra": 1}])
def test_invalid_contract_has_no_effect(client, change):
    assert send(client, **change).status_code == 422
    assert client.get("/sim/ledger").json()["effects"] == []


@pytest.mark.parametrize("change", [{"approval_id": "denied"}, {"amount_minor": 3000}])
def test_approval_is_bound_to_exact_payload(client, change):
    assert send(client, **change).status_code == 403
    assert client.get("/sim/ledger").json()["effects"] == []


def test_unknown_payment_operation_and_missing_key(client):
    assert send(client, payment_id="unknown").status_code == 404
    assert client.get("/sim/operations/missing").status_code == 404
    assert client.post("/sim/refunds", json=payload()).status_code == 422
    assert client.get("/sim/ledger").json()["effects"] == []


def test_app_instances_start_with_independent_ledgers(client):
    send(client)
    with TestClient(create_app()) as other:
        assert other.get("/sim/ledger").json()["refunded_minor"] == 0


def test_concurrent_replays_produce_one_effect():
    service = seeded_service()
    request = RefundRequest(**payload())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.refund("same_key", request), range(12)))
    assert all(result == results[0] for result in results)
    assert len(service.ledger().effects) == 1


def test_concurrent_distinct_refunds_cannot_exceed_capture():
    requests = [
        RefundRequest(
            **payload(intent_id=f"intent_{i}", approval_id=f"approval_{i}", amount_minor=6000)
        )
        for i in range(2)
    ]
    service = FakeTransactionService(
        FakePayment(payment_id="payment_demo_001", order_id="order_demo_001", captured_minor=10000),
        tuple(Approval(**request.model_dump()) for request in requests),
    )

    def attempt(request):
        try:
            service.refund(request.intent_id, request)
            return 200
        except ServiceError as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, requests))
    assert sorted(statuses) == [200, 409]
    assert service.ledger().refunded_minor == 6000
    assert len(service.ledger().effects) == 1
