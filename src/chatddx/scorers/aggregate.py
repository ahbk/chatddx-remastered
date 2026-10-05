import math
import statistics
from collections.abc import Callable


def mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def std(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def var(values: list[float]) -> float:
    return statistics.variance(values) if len(values) > 1 else 0.0


def stderr(values: list[float]) -> float:
    return std(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0


AGGREGATES: dict[str, Callable[[list[float]], float]] = {
    "mean": mean,
    "stderr": stderr,
    "std": std,
    "var": var,
}
