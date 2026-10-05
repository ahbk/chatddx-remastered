from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import Field, JsonValue, model_validator

from chatddx.factors.base import (
    Finding,
    Fingerprint,
    Frozen,
    canonical_bytes,
    sorted_keys,
)
from chatddx.factors.trial import Execution, Trial

from .record import ItemKey, UtcDatetime, in_order


class Call(Frozen):
    request: Fingerprint
    # With retries, from the first attempt's start to the last attempt's end.
    started_at: UtcDatetime
    finished_at: UtcDatetime
    status: int | None = None
    response: dict[str, JsonValue] | None = None
    prompt_tokens: Fingerprint | None = None
    attempts: int = Field(default=1, ge=1)
    error: str | None = None

    @model_validator(mode="after")
    def _times(self) -> "Call":
        in_order(self.started_at, self.finished_at)
        return self

    @property
    def returned_model(self) -> str | None:
        model = (self.response or {}).get("model")
        return model if isinstance(model, str) else None

    @property
    def system_fingerprint(self) -> str | None:
        fp = (self.response or {}).get("system_fingerprint")
        return fp if isinstance(fp, str) else None


def fingerprint_prompt_tokens(
    response: dict[str, JsonValue], key: tuple[str, bytes] | None = None
) -> tuple[dict[str, JsonValue], Fingerprint | None]:
    # prompt_token_ids encode the case text, so only their fingerprint may be stored.
    kept = {k: v for k, v in response.items() if k != "prompt_token_ids"}
    ids = response.get("prompt_token_ids")
    if not isinstance(ids, list):
        return kept, None
    return kept, Fingerprint.of(canonical_bytes(ids), key)


def fingerprint_request(
    body: dict[str, JsonValue], key: tuple[str, bytes] | None = None
) -> Fingerprint:
    return Fingerprint.of(canonical_bytes(sorted_keys(body)), key)


# One tool the runner ran for a tool call in a response: `result` is the text the model
# read back, `error` why the tool failed. A call naming no tool of the skeleton fails.
class ToolRun(Frozen):
    id: str
    name: str
    started_at: UtcDatetime
    finished_at: UtcDatetime
    result: str
    error: str | None = None

    @model_validator(mode="after")
    def _times(self) -> "ToolRun":
        in_order(self.started_at, self.finished_at)
        return self


# A tool round: the tools run for the previous response's calls, and the next call.
class Turn(Frozen):
    tools: tuple[ToolRun, ...] = Field(min_length=1)
    call: Call


# `sent` holds when each item was sent: for a run its first call, for a score its first
# judge call.
def check_execution(
    execution: Execution,
    trial: Trial,
    calls: Sequence[Call],
    sent: Mapping[ItemKey, datetime],
) -> list[Finding]:
    findings: list[Finding] = []
    allowed = execution.retries + 1
    if over := sum(1 for c in calls if c.attempts > allowed):
        findings.append(
            Finding(
                code="execution.retries",
                message=f"{over} calls took more than the {allowed} attempts allowed",
            )
        )

    position = {
        ItemKey(case=c, replicate=r): n
        for n, (c, r) in enumerate(execution.schedule(trial.cases, len(trial.seeds)))
    }
    early = 0
    latest: datetime | None = None
    for key in sorted(sent.keys() & position.keys(), key=position.__getitem__):
        at = sent[key]
        if latest is not None and at < latest:
            early += 1
        latest = at if latest is None else max(latest, at)
    if early:
        findings.append(
            Finding(
                code="execution.order",
                message=f"{early} items were sent before items scheduled ahead of them",
            )
        )

    # Ends sort before starts at the same instant: a call ending as another starts
    # doesn't overlap it.
    events = sorted(
        [(c.started_at, 1) for c in calls] + [(c.finished_at, -1) for c in calls]
    )
    in_flight = peak = 0
    for _, step in events:
        in_flight += step
        peak = max(peak, in_flight)
    if peak > execution.concurrency:
        findings.append(
            Finding(
                code="execution.concurrency",
                message=f"{peak} calls were in flight at once, declared "
                + f"{execution.concurrency}",
            )
        )
    return findings
