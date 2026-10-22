# Agent Transaction Lab

A local learning project for testing simulated transaction behavior. No real money or customer accounts are used.

## Day 1 starting point

Python package, FastAPI app factory and tested `GET /health` endpoint. No database, agent, recovery or frontend yet.

## Windows PowerShell setup

Run from the repository root with Python 3.10 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn agent_transaction_lab.main:create_app --factory --app-dir src --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs or http://127.0.0.1:8000/health. Stop with Ctrl+C.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m pip check
```

No environment variables or API keys are needed. Planning documents, local learning notes and virtual environments are intentionally ignored by Git.
