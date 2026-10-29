# Ledger integrity evaluator

`agent_transaction_lab.oracle.evaluate_ledger` reads a complete single-payment
snapshot, approved payloads and committed-operation records. It returns stable
violation codes and aggregate counts/totals. It has no network/database client,
does not call service safety/hash functions, and cannot change the service.

Checks cover exact authorization, payment/currency scope, duplicate effect IDs,
operation keys and logical intents, the sum of actual effects versus reported
balances/capture, operation-to-effect correspondence and canonical payload hashes.
Duplicate operation and approval records are also reported. Money uses the existing
integer-minor-unit contracts. Exported reports contain no request or approval IDs.

The current input contract represents committed operations only. Pending/rejected
status assessment and controller claims belong to the subsequent scenario/run
evaluation layer. A consistent empty ledger does not prove a refund completed.
This report must not be used as a completion-rate or agent-correctness score alone.

The caller must collect all inputs independently from authoritative storage in a
coherent transaction/snapshot and one run/payment scope. The evaluator does not
establish that a supplied snapshot is complete or came from a database. The existing
demo is not rewired to this evaluator yet; its independent database collector and
run-scoped integration are still pending. Agent messages, trace events and
checkpoint state must not be substituted for missing service evidence.

Run `python -m pytest tests/test_oracle.py` in the configured project environment.
Tests include a normal simulator snapshot and deliberately inconsistent fixtures:
changed authorization, cross-payment effects, duplicate rows/intents, forged balance
totals hiding over-refund, missing/extra/mismatched operations and invalid hashes.
No live PostgreSQL login or process kill is performed by these tests.

Limit: offline evaluator tests do not prove database transactions, durable intent
storage, actual crash recovery or the complete nine-scenario/four-controller matrix.
Those remain required alongside LangGraph checkpoints, dashboard and packaging.
