from fastapi.testclient import TestClient

from agent_transaction_lab.main import create_app


def test_health_identifies_simulation():
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "simulation"}
