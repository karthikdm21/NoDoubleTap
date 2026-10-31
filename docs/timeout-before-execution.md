# Timeout before execution (scripted simulator)

`before_execution.py` adds one fixed scenario. A fresh
`BeforeExecutionTimeoutTransport` raises an exception on its first refund attempt
before calling the simulated service. Tests verify no operation/effect exists at
that boundary. Subsequent calls reach the existing service unchanged.

The scripted recovery policy sends once, queries the same key, then permits at
most one same-key/same-payload retry after a 404. Not-found does not prove an earlier
request cannot commit; a test deliberately hides an already committed status and
checks that service idempotency still prevents a second effect. An unavailable
status stays unknown without retry. A matching committed status needs no retry.

Any accepted response must match the original key, effect key, full request and
canonical payload hash. A mismatched status/acknowledgement stays unknown. A lost
retry response ends unknown with `retry_exhausted`, even if the actual ledger has
one effect. Service rejections propagate instead of becoming success. Full denied
scenario reporting remains separate future work.

Run the fixed demo from the project root in the configured environment:

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m agent_transaction_lab.before_execution
```

The JSON report uses `scenario_id=timeout_before_execution_v1`. Expected: two
refund attempts, one status query, one effect of 2500 and 7500 remaining. The first
five events retain outcome unknown: refund attempt, response lost, status query,
status not found and retry attempt. The acknowledged result and final outcome then
become committed. Sequence numbers express order, not elapsed time.

The report's field shape/schema_version remains 1. The existing after-commit ID,
default demo and command retain their behavior. The scenario-ID allowlist now also
accepts this before-execution demo, with `status_not_found` and `retry_exhausted`
added to the event-kind allowlist. Counters measure controller transport calls;
independent inspection reads are separate. The demo evaluates actual ledger and
stored operations with the ledger-integrity evaluator, not the controller result.
Ledger assertions and caller outcome remain distinct evidence.

Run `python -m pytest tests/test_before_execution.py tests/test_scenario.py` in
the configured environment to check the new and original scenario behavior.

Limits: fixed in-memory fixture, synchronous scripted policy, two-send/one-query
call budget. No elapsed-time deadline, real packet drop, process kill, persisted
identity, PostgreSQL acceptance, LangGraph checkpoint or hosted AI agent is proved.
Each run needs a fresh service/wrapper. The full nine families/four controllers,
durable recovery, dashboard and reproducible packaging remain required. This is
one additional deterministic scenario slice, not a complete fault engine.
