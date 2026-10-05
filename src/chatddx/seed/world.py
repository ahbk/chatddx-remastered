from chatddx.factors.engine import LocalEngine
from chatddx.inventory.inventory import Inventory
from chatddx.inventory.serving import start_up, url_of

from .plan import Plan
from .write import short


def endpoints(inventory: Inventory, plan: Plan) -> list[str]:
    seeded: dict[str, list[str]] = {}
    for r in plan.records:
        seeded.setdefault(r.digest, []).append(f"{r.kind} {r.name}")
    lines: list[str] = []
    for name, endpoint in inventory.endpoints.items():
        what = seeded.get(endpoint.engine)
        if what is None:
            lines.append(
                f"[world endpoint] {name}: serves {short(endpoint.engine)}, "
                + "which the data doesn't seed"
            )
            continue
        get = plan.registry.get
        try:
            url = url_of(inventory, get, name)
            if isinstance(get(endpoint.engine), LocalEngine):
                _ = start_up(inventory, get, name)
        except LookupError as e:
            lines.append(f"[world endpoint] {name}: {e}")
            continue
        lines.append(
            f"[world endpoint] {name}: {url} serves {', '.join(what)} "
            + short(endpoint.engine)
        )
    return lines
