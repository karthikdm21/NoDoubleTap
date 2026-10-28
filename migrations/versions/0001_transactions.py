"""First committed-refund schema. Frozen DDL, not imported runtime metadata."""

from alembic import op

revision = "0001_transactions"
down_revision = None
branch_labels = None
depends_on = None

DDL = [
    """CREATE TABLE public.ndt_runs (
        run_id UUID CONSTRAINT ndt_runs_pk PRIMARY KEY,
        seed_digest TEXT NOT NULL)""",
    """CREATE TABLE public.ndt_payments (
        run_id UUID NOT NULL REFERENCES public.ndt_runs(run_id),
        payment_id TEXT NOT NULL, order_id TEXT NOT NULL,
        currency TEXT NOT NULL CONSTRAINT ndt_currency CHECK (currency = 'INR'),
        captured_minor BIGINT NOT NULL CONSTRAINT ndt_capture CHECK (captured_minor > 0),
        refunded_minor BIGINT NOT NULL DEFAULT 0,
        CONSTRAINT ndt_balance CHECK (refunded_minor >= 0 AND refunded_minor <= captured_minor),
        CONSTRAINT ndt_payments_pk PRIMARY KEY (run_id, payment_id),
        CONSTRAINT ndt_payment_currency UNIQUE (run_id, payment_id, currency))""",
    """CREATE TABLE public.ndt_approvals (
        run_id UUID NOT NULL, approval_id TEXT NOT NULL, intent_id TEXT NOT NULL,
        payment_id TEXT NOT NULL, amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
        currency TEXT NOT NULL,
        CONSTRAINT ndt_approvals_pk PRIMARY KEY (run_id, approval_id),
        CONSTRAINT ndt_approved_tuple UNIQUE
            (run_id, approval_id, intent_id, payment_id, amount_minor, currency),
        CONSTRAINT ndt_approval_payment FOREIGN KEY (run_id, payment_id, currency)
            REFERENCES public.ndt_payments(run_id, payment_id, currency))""",
    """CREATE TABLE public.ndt_intents (
        run_id UUID NOT NULL, intent_id TEXT NOT NULL, operation_key TEXT NOT NULL,
        payment_id TEXT NOT NULL, amount_minor BIGINT NOT NULL, currency TEXT NOT NULL,
        approval_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
        CONSTRAINT ndt_intents_pk PRIMARY KEY (run_id, intent_id),
        CONSTRAINT ndt_intent_key UNIQUE (run_id, operation_key),
        CONSTRAINT ndt_intent_binding UNIQUE (run_id, operation_key, intent_id, payload_hash),
        CONSTRAINT ndt_intent_payload UNIQUE
            (run_id, intent_id, operation_key, payment_id, amount_minor, currency, approval_id),
        CONSTRAINT ndt_intent_approval FOREIGN KEY
            (run_id, approval_id, intent_id, payment_id, amount_minor, currency)
            REFERENCES public.ndt_approvals
            (run_id, approval_id, intent_id, payment_id, amount_minor, currency))""",
    """CREATE TABLE public.ndt_operations (
        run_id UUID NOT NULL, operation_key TEXT NOT NULL, intent_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL, status TEXT NOT NULL CHECK (status = 'committed'),
        CONSTRAINT ndt_operations_pk PRIMARY KEY (run_id, operation_key),
        CONSTRAINT ndt_operation_intent UNIQUE (run_id, intent_id),
        CONSTRAINT ndt_operation_tuple UNIQUE (run_id, operation_key, intent_id),
        CONSTRAINT ndt_operation_binding FOREIGN KEY
            (run_id, operation_key, intent_id, payload_hash) REFERENCES public.ndt_intents
            (run_id, operation_key, intent_id, payload_hash))""",
    """CREATE TABLE public.ndt_effects (
        run_id UUID NOT NULL, effect_id TEXT NOT NULL, operation_key TEXT NOT NULL,
        intent_id TEXT NOT NULL, payment_id TEXT NOT NULL, amount_minor BIGINT NOT NULL,
        currency TEXT NOT NULL, approval_id TEXT NOT NULL,
        CONSTRAINT ndt_effects_pk PRIMARY KEY (run_id, effect_id),
        CONSTRAINT ndt_effect_key UNIQUE (run_id, operation_key),
        CONSTRAINT ndt_effect_intent UNIQUE (run_id, intent_id),
        CONSTRAINT ndt_effect_operation FOREIGN KEY (run_id, operation_key, intent_id)
            REFERENCES public.ndt_operations(run_id, operation_key, intent_id),
        CONSTRAINT ndt_effect_payload FOREIGN KEY
            (run_id, intent_id, operation_key, payment_id, amount_minor, currency, approval_id)
            REFERENCES public.ndt_intents
            (run_id, intent_id, operation_key, payment_id, amount_minor, currency, approval_id))""",
    """ALTER TABLE public.ndt_operations ADD CONSTRAINT ndt_committed_effect
        FOREIGN KEY (run_id, operation_key) REFERENCES public.ndt_effects(run_id, operation_key)
        DEFERRABLE INITIALLY DEFERRED""",
    """CREATE FUNCTION public.ndt_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Immutable simulation binding' USING ERRCODE = '23514';
        END; $$""",
    """CREATE FUNCTION public.ndt_payment_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Immutable payment' USING ERRCODE = '23514';
            END IF;
            IF ROW(OLD.run_id, OLD.payment_id, OLD.order_id, OLD.currency, OLD.captured_minor)
                IS DISTINCT FROM
                ROW(NEW.run_id, NEW.payment_id, NEW.order_id, NEW.currency, NEW.captured_minor)
            THEN RAISE EXCEPTION 'Immutable payment payload' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END; $$""",
]


def upgrade():
    for statement in DDL:
        op.execute(statement)
    for table in ("runs", "approvals", "intents", "operations", "effects"):
        op.execute(
            f"CREATE TRIGGER ndt_{table}_immutable BEFORE UPDATE OR DELETE "
            f"ON public.ndt_{table} FOR EACH ROW EXECUTE FUNCTION public.ndt_immutable()"
        )
    op.execute(
        "CREATE TRIGGER ndt_payment_guard BEFORE UPDATE OR DELETE "
        "ON public.ndt_payments FOR EACH ROW EXECUTE FUNCTION public.ndt_payment_guard()"
    )


def downgrade():
    op.execute("ALTER TABLE public.ndt_operations DROP CONSTRAINT ndt_committed_effect")
    for table in ("effects", "operations", "intents", "approvals", "payments", "runs"):
        op.execute(f"DROP TABLE public.ndt_{table}")
    op.execute("DROP FUNCTION public.ndt_payment_guard()")
    op.execute("DROP FUNCTION public.ndt_immutable()")
