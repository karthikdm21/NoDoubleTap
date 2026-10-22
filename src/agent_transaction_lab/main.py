"""HTTP entry point. Each app owns its own simulation state."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Transaction Lab", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "simulation"}

    return app
