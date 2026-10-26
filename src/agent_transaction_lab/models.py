"""Strict, immutable contracts for fake transactions. Amounts are minor units."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")]
Amount = Annotated[int, Field(strict=True, gt=0)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FakePayment(Contract):
    payment_id: Identifier
    order_id: Identifier
    currency: Literal["INR"] = "INR"
    captured_minor: Amount


class RefundRequest(Contract):
    intent_id: Identifier
    payment_id: Identifier
    amount_minor: Amount
    currency: Literal["INR"]
    approval_id: Identifier


class Approval(RefundRequest):
    """A seeded test permission for one exact refund payload, not real authentication."""


class RefundEffect(Contract):
    effect_id: Identifier
    operation_key: Identifier
    request: RefundRequest


class Operation(Contract):
    operation_key: Identifier
    payload_hash: str
    status: Literal["committed"] = "committed"
    effect: RefundEffect


class LedgerSnapshot(Contract):
    payment: FakePayment
    refunded_minor: int
    remaining_minor: int
    effects: tuple[RefundEffect, ...]
