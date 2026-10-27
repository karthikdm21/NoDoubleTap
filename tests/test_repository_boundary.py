"""Adapter wiring checks, not database integration or restart evidence."""

from fastapi.testclient import TestClient

from agent_transaction_lab.main import create_app
from agent_transaction_lab.models import LedgerSnapshot, Operation, RefundRequest
from agent_transaction_lab.repository import TransactionRepository
from agent_transaction_lab.scenario import ResponseLossTransport, reconcile_refund
from agent_transaction_lab.service import ServiceError, seeded_service


def request():
    return RefundRequest(
        intent_id="intent_demo_001",
        payment_id="payment_demo_001",
        amount_minor=2500,
        currency="INR",
        approval_id="approval_demo_001",
    )


def test_two_http_apps_reconcile_one_injected_repository():
    repository: TransactionRepository = seeded_service()
    with TestClient(create_app(repository)) as first, TestClient(create_app(repository)) as second:
        response = first.post(
            "/sim/refunds",
            headers={"Idempotency-Key": "original_key"},
            json=request().model_dump(),
        )
        assert response.status_code == 200
        assert second.get("/sim/operations/original_key").json() == response.json()
        replay = second.post(
            "/sim/refunds",
            headers={"Idempotency-Key": "original_key"},
            json=request().model_dump(),
        )
        assert replay.json() == response.json()
        ledger = second.get("/sim/ledger").json()
        assert len(ledger["effects"]) == 1
        assert ledger["refunded_minor"] == 2500


class UnavailableRepository:
    def refund(self, key: str, request: RefundRequest) -> Operation:
        raise ServiceError(503, "Simulation store unavailable")

    def operation(self, key: str) -> Operation:
        raise ServiceError(503, "Simulation store unavailable")

    def ledger(self) -> LedgerSnapshot:
        raise ServiceError(503, "Simulation store unavailable")


def test_unavailable_repository_does_not_silently_fall_back_to_fresh_memory():
    with TestClient(create_app(UnavailableRepository())) as client:
        assert (
            client.post(
                "/sim/refunds",
                headers={"Idempotency-Key": "original_key"},
                json=request().model_dump(),
            ).status_code
            == 503
        )
        assert client.get("/sim/operations/original_key").status_code == 503
        assert client.get("/sim/ledger").status_code == 503


def test_scenario_can_read_an_unavailable_repository_without_claiming_success():
    # A future database adapter can fail independently of the controller.
    repository: TransactionRepository = UnavailableRepository()
    transport = ResponseLossTransport(repository)
    try:
        reconcile_refund(transport, "original_key", request())
    except ServiceError as error:
        assert error.status_code == 503
    else:
        raise AssertionError("Unavailable storage must not become a success report")
    assert transport.refund_attempts == 1
    assert transport.status_queries == 0
