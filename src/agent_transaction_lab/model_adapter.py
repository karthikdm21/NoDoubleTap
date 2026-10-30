"""Validated tool-call boundary and deterministic fixtures; no hosted model client."""

from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, ValidationError

from agent_transaction_lab.models import Contract, Identifier, Operation, RefundRequest
from agent_transaction_lab.repository import TransactionRepository


class RefundToolCall(Contract):
    tool: Literal["refund"]
    operation_key: Identifier
    arguments: RefundRequest


class StatusToolCall(Contract):
    tool: Literal["operation_status"]
    operation_key: Identifier


ToolCall = Annotated[RefundToolCall | StatusToolCall, Field(discriminator="tool")]
_CALL: TypeAdapter[ToolCall] = TypeAdapter(ToolCall)
_LIMIT = TypeAdapter(Annotated[int, Field(strict=True, ge=1, le=32)])
MAX_OUTPUT_BYTES = 16384


class InvalidModelOutput(ValueError):
    """A rejected output; message deliberately excludes raw input and validator details."""


class ModelCallBudgetExhausted(RuntimeError):
    """No more decisions may be requested from this adapter instance."""


class ModelFixtureExhausted(RuntimeError):
    """The deterministic response fixture has no output for this call."""


def parse_tool_call(output: str) -> ToolCall:
    """Accept exactly one allowlisted JSON tool call with strict immutable arguments."""
    if not isinstance(output, str):
        raise InvalidModelOutput("Expected one JSON tool call")
    try:
        if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise InvalidModelOutput("Model output exceeds the tool-call size limit")
        return _CALL.validate_json(output, strict=True)
    except (ValidationError, UnicodeError):
        raise InvalidModelOutput("Invalid or unsupported JSON tool call") from None


class ModelAdapter(Protocol):
    """Stateful decision boundary. A caller dispatches validated calls explicitly."""

    def next_call(self) -> ToolCall: ...


class ScriptedModelAdapter:
    """Replay JSON fixtures; invalid/missing outputs consume the decision budget too.

    A fresh instance is required for each run. These are scripted decisions, not
    model inference, tokens or measured provider calls. No fallback/network exists.
    """

    def __init__(self, outputs: tuple[str, ...], *, max_calls: int = 8) -> None:
        if not isinstance(outputs, tuple) or any(not isinstance(o, str) for o in outputs):
            raise ValueError("Fixture outputs must be a tuple of JSON strings")
        try:
            self._limit = _LIMIT.validate_python(max_calls)
        except ValidationError:
            raise ValueError("max_calls must be an integer from 1 to 32") from None
        self._outputs = outputs
        self._calls_used = 0

    @property
    def calls_used(self) -> int:
        return self._calls_used

    def next_call(self) -> ToolCall:
        if self._calls_used >= self._limit:
            raise ModelCallBudgetExhausted("Scripted decision budget exhausted")
        index = self._calls_used
        self._calls_used += 1
        if index >= len(self._outputs):
            raise ModelFixtureExhausted("No scripted output remains")
        return parse_tool_call(self._outputs[index])


def dispatch_tool_call(call: ToolCall, repository: TransactionRepository) -> Operation:
    """Dispatch validated calls unchanged; service constraints remain authoritative.

    No new key, automatic retry, status inference or error-to-success conversion.
    This function cannot execute arbitrary names, code or real payment tools.
    """
    if isinstance(call, RefundToolCall):
        return repository.refund(call.operation_key, call.arguments)
    return repository.operation(call.operation_key)
