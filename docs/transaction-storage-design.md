# Transaction storage design (foundation)

Status: proposed PostgreSQL schema and migration contract; NOT an applied migration.
The runnable backend is still in memory. No SQLAlchemy/Alembic/driver version is
selected or installed by this step. No database, role, password or schema is created.

## Implemented boundary

`TransactionRepository` owns refund enforcement, status lookup and ledger reads.
`create_app(repository=...)` and `ResponseLossTransport` accept that interface.
The existing fake service is the default implementation. Each default app remains
independent; explicitly sharing a repository shares its scope. An unavailable
injected repository must fail rather than silently create an empty memory store.
This is dependency injection, not persistence. The synchronous boundary fits the
current synchronous routes; a future adapter must own its sessions per operation,
never share one Session among concurrent requests or keep a transaction open while
waiting on a model/tool response.

The first interface preserves today's committed-only status contract. Later work
must explicitly extend statuses for pending/rejected operations and add run-bound
construction; it must not treat a missing operation as proof of non-execution.

## Planned first schema revision

All identifiers below are synthetic. IDs are scoped by run_id (UUID). Composite
foreign keys must include run_id so one run cannot reference another run's data.
Money fields use BIGINT integer minor units with positive/nonnegative checks.
Every constraint needs an explicit stable name for migration/error mapping.

| Table | Key and important fields | Constraints / purpose |
| --- | --- | --- |
| simulation_runs | run_id PK, scenario_version, seed, initial_digest | Immutable seed/config identity; lifecycle/events added separately |
| fake_orders | (run_id, order_id) PK | FK to run |
| fake_payments | (run_id, payment_id) PK, order_id, currency, captured_minor, refunded_minor | FK order; INR initially; captured > 0; 0 <= refunded <= captured |
| refund_approvals | (run_id, approval_id) PK, intent_id, payment_id, currency, amount_minor | Exact immutable approved payload; UNIQUE all bound fields for composite reference; no client-created approvals |
| refund_intents | (run_id, intent_id) PK, operation_key, approval/payload fields, payload_hash | UNIQUE (run_id, operation_key); composite FK to exact approval tuple; FK payment; immutable after creation |
| refund_operations | (run_id, operation_key) PK, intent_id, payload_hash, status | UNIQUE (run_id, intent_id); FK to pre-persisted exact intent/key/hash tuple; committed-only in first service revision |
| refund_effects | (run_id, effect_id) PK, operation_key, intent_id, payment_id, currency, amount_minor | UNIQUE (run_id, operation_key), UNIQUE (run_id, intent_id); composite FK binds operation/intent and approved payload |

The intent table will need additional explicit UNIQUE tuples to support exact
composite foreign keys from operations/effects. Do not approximate these with
independent FKs that allow mixing fields from different approved actions.
Store no credentials, model prose or request exception messages in these tables.
Keep agent/checkpointer tables separate from the service ledger.

Immutable bindings require enforcement: migration-defined BEFORE UPDATE guards
for approved payloads, intent/key/hash and committed operation/effect rows; use
append-only effects. Application code alone must not silently mutate prior hashes.
Balance updates are the one mutable payment value. Reset deletes only a named
simulation run in a dedicated test environment, with no broad database drop.
Runtime permission hardening remains a later explicit setup step; these constraints
do not claim financial production readiness or protection from a database admin.

## Transaction algorithm to implement and prove

1. Orchestration durably creates the run and approved immutable intent/key before
   dispatch. Identity creation is separate from a refund effect. Until orchestration
   exists, database fixtures create this intent before service contract tests.
2. Validate strict request/key format. Begin a SQLAlchemy transaction scope.
3. Lock the persisted intent row, verify the key/hash and full canonical payload,
   then lock its payment row using SELECT FOR UPDATE. Use this lock order everywhere.
   Same-intent calls serialize; different intents sharing a payment serialize on
   that payment. Multi-payment operations are outside this first revision.
4. Re-read an existing committed operation after acquiring locks. Return its stored
   result only for the identical payload; reject changed key/payload with 409.
   Unique key AND intent constraints defend concurrent duplicate logical actions.
5. Validate the exact approval and payment/currency; use a conditional balance
   update with `refunded_minor + amount_minor <= captured_minor`. A zero-row update
   is a limit failure. Keep the payment CHECK as a second guard. A cross-row SUM
   cannot be enforced with a PostgreSQL CHECK constraint.
6. Insert the operation and append exactly one effect in that SAME transaction.
   Any failed insert/constraint/commit rolls back the balance change as well.
   Return a committed result only after commit succeeds. A lost commit acknowledgement
   remains unknown to the caller; reconcile the original key on a fresh connection.
7. Translate named conflicts only after rollback; status reads use a fresh transaction.
   A not-found status never authorizes a new key. Do not swallow connection failures
   as a new seed or infer successful recovery from an agent checkpoint.

Canonical hash inputs remain the sorted compact JSON RefundRequest payload (intent,
payment, amount, currency, approval) plus immutable run binding by repository scope.
Do not accept a client-supplied hash as proof. Version the canonicalization before
changing it. The existing seed is payment_demo_001, capture 10000 INR, approval
approval_demo_001 for intent_demo_001 / 2500; operation key assigned before dispatch.

## Alembic, seed and independent oracle exits

Planned commands only, until an actual migration environment defines them:
`alembic upgrade head` creates named tables/constraints/immutability guards in a
dedicated simulation database. A reviewed revision implements upgrade and downgrade;
downgrade is destructive and must run only in a disposable integration database.
Never run autogenerate output without review. Keep URLs out of alembic.ini and logs;
use DATABASE_URL from an ignored local environment. No real values in examples.

Seed in one transaction under an explicit run_id. Repeating an identical seed is
idempotent; a differing existing digest is a conflict, never an implicit overwrite.
The later reset command must require an explicit run and refuse non-test contexts.
Seed fixtures for a valid refund, denied approval and competing 6000-unit intents
against a 10000 capture. Tests create their own namespaces and clean up only those.

The independent oracle reads effects/approvals/payment rows on a separate connection,
not through repository success responses. Assert exact payload authorization,
unique logical effect, SUM(effects.amount_minor) = payments.refunded_minor <= capture,
and committed-operation/effect correspondence. Agent state is not the oracle.

Database acceptance still required: migrate empty database and round-trip disposable
downgrade/upgrade; seed repeat/conflict; same-key concurrency; conflicting payload;
second key/same intent; two authorized refunds exceeding capture; failure after balance
update before effect insert (total rollback); separate connection/reopen status;
cross-run reference rejection; mutation rejection; lost commit acknowledgement.
These must run against PostgreSQL, not SQLite or a fake repository. No such database
integration test has passed in this step. Actual owned-worker process kills,
LangGraph durable checkpoints, nine fault families/four controllers, dashboard and
packaging remain subsequent original-scope requirements.

## Primary references

- [PostgreSQL row locking](https://www.postgresql.org/docs/18/explicit-locking.html)
- [PostgreSQL constraints and cross-row CHECK limits](https://www.postgresql.org/docs/18/ddl-constraints.html)
- [SQLAlchemy transaction scopes](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
- [Alembic migration environment](https://alembic.sqlalchemy.org/en/latest/tutorial.html)

Reviewed 27 October 2026. References explain the design; they do not select dependency
versions or establish that this proposed database implementation has been tested.
