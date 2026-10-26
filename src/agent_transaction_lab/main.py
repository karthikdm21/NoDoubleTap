"""HTTP entry point. Each app owns its own simulation state."""

from typing import Annotated

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from agent_transaction_lab.models import LedgerSnapshot, Operation, RefundRequest
from agent_transaction_lab.service import ServiceError, seeded_service


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Transaction Lab", version="0.1.0")
    service = seeded_service()

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "simulation"}

    @app.post("/sim/refunds", response_model=Operation)
    def refund(
        request: RefundRequest,
        idempotency_key: Annotated[
            str, Header(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
        ],
    ) -> Operation:
        return service.refund(idempotency_key, request)

    @app.get("/sim/operations/{key}", response_model=Operation)
    def operation(key: str) -> Operation:
        return service.operation(key)

    @app.get("/sim/ledger", response_model=LedgerSnapshot)
    def ledger() -> LedgerSnapshot:
        return service.ledger()

    return app
