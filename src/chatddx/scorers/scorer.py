from collections.abc import Callable, Mapping
from typing import NamedTuple, Protocol, cast

from pydantic import JsonValue

from chatddx.core.rig import entry
from chatddx.factors.scoring import Scorer


# One view's score: its value, or None when the view can't be scored, and what a person
# reads to see why. The ledger keeps both (src/chatddx/ledger/score.py:ScoreItem).
class Scored(NamedTuple):
    value: float | None
    detail: JsonValue = None


# A scorer's entry point: for a view's metric, the items its output selector picks (None
# when the run has no answer) and those its expectation selector picks, with the
# scorer's params under the view's.
class ScoreFunction(Protocol):
    def __call__(
        self,
        metric: str,
        output: list[JsonValue] | None,
        expected: list[JsonValue],
        params: Mapping[str, JsonValue],
    ) -> Scored: ...


Metric = Callable[
    [list[JsonValue] | None, list[JsonValue], Mapping[str, JsonValue]], Scored
]


class Metrics:
    def __init__(self, **metrics: Metric) -> None:
        self._metrics: dict[str, Metric] = metrics

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._metrics)

    def __call__(
        self,
        metric: str,
        output: list[JsonValue] | None,
        expected: list[JsonValue],
        params: Mapping[str, JsonValue],
    ) -> Scored:
        found = self._metrics.get(metric)
        if found is None:
            raise ValueError(f"no metric {metric!r}, only {sorted(self._metrics)}")
        return found(output, expected, params)


def function(scorer: Scorer) -> ScoreFunction:
    found = entry(scorer.code, scorer.entry_point)
    if not callable(found):
        raise TypeError(f"{scorer.entry_point} isn't a function")
    names = getattr(found, "names", None)
    if isinstance(names, frozenset):
        known = cast(frozenset[str], names)
        if unknown := sorted({v.metric for v in scorer.views} - known):
            raise LookupError(
                f"{scorer.entry_point} knows no metric {', '.join(unknown)}, "
                + f"only {', '.join(sorted(known))}"
            )
    return cast(ScoreFunction, found)


def score(
    scorer: Scorer, run: ScoreFunction, answer: JsonValue | None, data: JsonValue
) -> list[Scored]:
    if judged := [i for i, v in enumerate(scorer.views) if v.judge is not None]:
        raise NotImplementedError(f"views {judged} need a judge, which can't run yet")
    return [
        run(
            view.metric,
            None if answer is None else view.output_items(answer),
            view.expectation_items(data),
            scorer.params | view.params,
        )
        for view in scorer.views
    ]
