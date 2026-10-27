"""Storage boundary for one simulation scope; no persistence implementation here."""

from typing import Protocol

from agent_transaction_lab.models import LedgerSnapshot, Operation, RefundRequest


class TransactionRepository(Protocol):
    """Bind an instance to one run/payment scope before giving it to a caller.

    Implementations own approval, balance, key/payload and intent enforcement.
    A durable implementation must commit operation, effect and balance together
    before returning. A timeout is unknown; callers reconcile the original key.
    The existing fake service satisfies this interface in memory only.
    """

    def refund(self, key: str, request: RefundRequest) -> Operation:
        """Replay the same payload; reject conflicts without another effect."""
        ...

    def operation(self, key: str) -> Operation:
        """Read authoritative status in this scope; missing is not a failed send."""
        ...

    def ledger(self) -> LedgerSnapshot:
        """Return an immutable snapshot of service effects, separate from traces."""
        ...
