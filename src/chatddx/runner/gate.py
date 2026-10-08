import os

from chatddx.factors.base import Resolver, resolve
from chatddx.factors.cases import Case
from chatddx.factors.request import Skeleton
from chatddx.factors.trial import Trial
from chatddx.inventory.inventory import Inventory
from chatddx.inventory.serving import confirm


# Sending case-derived content where it isn't cleared to go is the one hard block.
class Refused(Exception):
    pass


def check(
    inventory: Inventory,
    get: Resolver,
    endpoint: str,
    trial: Trial,
    timeout: float = 10.0,
) -> None:
    try:
        spec = inventory.endpoint(endpoint)
        sources = sorted({resolve(get, c, Case).vignette.source for c in trial.cases})
        sensitive = [s for s in sources if inventory.source(s).sensitive]
    except LookupError as e:
        raise Refused(str(e)) from None
    if spec.engine != trial.engine:
        raise Refused(
            f"endpoint {endpoint!r} serves engine {spec.engine}, the trial's is "
            + trial.engine
        )
    if uncleared := sorted(set(sensitive) - inventory.cleared_for(endpoint)):
        raise Refused(
            f"endpoint {endpoint!r} isn't cleared for source {', '.join(uncleared)}, "
            + "which is sensitive"
        )
    if resolve(get, trial.skeleton, Skeleton).tools and sensitive:
        raise Refused(
            f"the skeleton's tools would get content from source {', '.join(sensitive)}, "
            + "which is sensitive, and tools can't be cleared yet"
        )
    try:
        confirm(inventory, get, endpoint, timeout)
    except (LookupError, OSError) as e:
        raise Refused(f"endpoint {endpoint!r} can't be confirmed: {e}") from None


# An endpoint's credential names the environment variable holding its secret.
def secret(inventory: Inventory, endpoint: str) -> str | None:
    credential = inventory.endpoint(endpoint).credential
    if credential is None:
        return None
    value = os.environ.get(credential)
    if not value:
        raise Refused(
            f"endpoint {endpoint!r} needs the secret in ${credential}, which isn't set"
        )
    return value
