# Validated model fixtures

`model_adapter.py` provides a small stateful `ModelAdapter` decision protocol and
`ScriptedModelAdapter`. A fixture returns predetermined JSON strings. Each fresh
instance has its own finite decision budget and starts at the first response.
There is no hosted provider, inference, network fallback or model training.
Scripted outputs are not measured model behavior.

Only these two JSON shapes are allowed:

```json
{"tool":"refund","operation_key":"original_key","arguments":{"intent_id":"intent_demo_001","payment_id":"payment_demo_001","amount_minor":2500,"currency":"INR","approval_id":"approval_demo_001"}}
```

```json
{"tool":"operation_status","operation_key":"original_key"}
```

Arguments use the existing strict immutable contracts: integer positive minor
units, exact field names, validated keys and no extra fields. Unsupported tool
names, malformed JSON, invalid Unicode and outputs exceeding 16 KiB are rejected.
The parser raises a generic `InvalidModelOutput` message without logging raw input
or validator details. Callers must likewise avoid logging raw model responses.

`next_call()` validates a decision; it does not execute a tool. Explicit
`dispatch_tool_call(call, repository)` forwards the key/payload unchanged to the
simulation service. No new key, automatic retry or success inference is added.
Approval, capture limits and duplicate protection remain service responsibilities.
A missing status is an error/unknown observation, not permission for a fresh key.

The default budget is eight decisions; configuration accepts strict integers from
1 to 32. Invalid responses and missing fixtures each consume an attempt. Once
exhausted, `ModelCallBudgetExhausted` blocks further output. A missing response
raises `ModelFixtureExhausted`; controllers must end or handle that explicitly.
`calls_used` counts scripted decision attempts, not tokens, paid API calls or cost.
This is not a controller retry/time budget or an elapsed-time deadline.

Example decision parsing (from the project environment with PYTHONPATH=src):

```python
from agent_transaction_lab.model_adapter import ScriptedModelAdapter

adapter = ScriptedModelAdapter(
    ('{"tool":"operation_status","operation_key":"original_key"}',),
    max_calls=1,
)
call = adapter.next_call()
assert call.operation_key == "original_key"
```

Run `python -m pytest tests/test_model_adapter.py` using the configured environment.
Tests inspect actual in-memory effects after dispatch, including invalid arguments,
authorization denial, duplicate intent/new key, missing status and budget exhaustion.
The fixtures currently live in tests and are identified by the code revision.

Still required: controller/run integration, recorded model/prompt/fixture versions,
durable intent identity, LangGraph checkpoints, PostgreSQL reconciliation, actual
process interruption, all nine fault families/four controllers, dashboard and
reproducible packaging. This adapter is not an AI agent or live database evidence.
Any future paid adapter needs a separate provider and budget decision.
