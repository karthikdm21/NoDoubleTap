# Agent Transaction Lab

A local learning project for testing simulated transaction behavior. No real money or customer accounts are used.

## Day 1 starting point

Implemented: a FastAPI app factory, health endpoint, strict Pydantic contracts and a deterministic in-memory refund service. Amounts are integer minor units (10000 INR paise = INR 100). One seeded payment has a capture of 10000 and approval for one refund of 2500.

Routes:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Identify the running simulation |
| POST | `/sim/refunds` | Submit the seeded fake refund with an `Idempotency-Key` header |
| GET | `/sim/operations/{key}` | Read a committed operation; unknown keys return 404 |
| GET | `/sim/ledger` | Inspect actual in-memory effects and remaining balance |

The same key and payload replay the original result. Conflicting payloads or a new key for an already committed intent return 409. Seeded approval must match the exact request; denied requests return 403. Invalid fields return 422, and unknown payments return 404.

## Limits of this foundation

State disappears on restart or reload. Run **one worker only**: the lock serializes threads in one process, not multiple servers. There is no database transaction or durable exactly-once guarantee. Only committed operations are stored; denied attempts are not yet an operation history. No run isolation API, fault injector, unknown/pending state machine, recovery, LangGraph, LLM, frontend, benchmark or production authentication exists yet. Seeded approval IDs are test fixtures, not secrets or real user authorization. Use loopback only.

Tests examine the ledger rather than trusting a success message. They cover validation, authorization, replay, conflicts, instance isolation, concurrent replays and competing refunds exceeding the capture. These are in-process tests, not crash-recovery tests.

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

## Try the fake refund

With the server running, open a second PowerShell window:

```powershell
$body = @{
    intent_id = 'intent_demo_001'
    payment_id = 'payment_demo_001'
    amount_minor = 2500
    currency = 'INR'
    approval_id = 'approval_demo_001'
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/sim/refunds' -ContentType 'application/json' -Headers @{'Idempotency-Key'='op_demo_001'} -Body $body
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/sim/ledger'
```

Repeat the POST unchanged: the ledger still has one effect, 2500 refunded and 7500 remaining. Restart the app to reset the demo. The same flow is available through `/docs`.

## Reading the code

- `src/agent_transaction_lab/main.py`: HTTP routes and error mapping.
- `models.py`: strict, immutable data contracts. Strings, floats and booleans cannot silently become money amounts.
- `service.py`: seeded fake payment, approvals, operation lookup and ledger changes.
- `tests/`: HTTP contract tests and direct service concurrency checks.

Tested on Windows with Python 3.10.0. `requirements-dev.txt` records the exact dependency set resolved and tested locally; it is a development snapshot, not a cross-platform lock with hashes. `pip check` validates installed dependency metadata. Source files are checked with strict mypy; tests are checked by pytest and Ruff.

Known dependency warning: the installed Starlette TestClient deprecates its HTTPX compatibility path. Tests currently pass with the pinned HTTPX version. The warning is intentionally visible; a future dependency update should migrate the test transport and rerun the suite.

Next small milestone: a deterministic after-commit response failure, then status reconciliation using the same operation key. Persistence and actual process-kill recovery come later.

References used for the foundation: [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/), [Pydantic strict mode](https://docs.pydantic.dev/latest/concepts/strict_mode/) and [FastAPI server startup](https://fastapi.tiangolo.com/deployment/manually/).
