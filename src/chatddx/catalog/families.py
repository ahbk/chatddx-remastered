from collections.abc import Mapping
from dataclasses import dataclass

from chatddx.factors.base import Fingerprint, Resolver, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Appendix, Case, Vignette
from chatddx.factors.scoring import Expectation

from .model import Behind, Binding, Subject, Survey, latest
from .read import Reader, about, heads_holding


@dataclass(frozen=True)
class RepairPlan:
    vignette: Vignette
    registry: Registry
    cases: dict[str, str]
    appendices: dict[str, str]
    moves: list[tuple[int, str]]


def bindings(rows: Reader, family: int) -> list[Binding]:
    found = rows.bindings(family)
    if not found:
        raise LookupError(f"no family with id {family}")
    return found


def family_of(rows: Reader, case: str) -> int | None:
    if rows.kind(case) != "case":
        return None
    vignette = resolve(rows.get, case, Case).vignette
    matching = [b for b in rows.bindings_in(vignette.source) if b.vignette == vignette]
    return matching[-1].family if matching else None


def holder(
    rows: Reader, vignette: Vignette, *, but: int | None = None
) -> tuple[int, bool] | None:
    current = latest((b.family, b) for b in rows.bindings_in(vignette.source))
    for family in sorted(current):
        held = current[family].vignette
        if family != but and (
            held.id == vignette.id or held.fingerprint == vignette.fingerprint
        ):
            return family, held.id == vignette.id
    return None


def check_adoptable(rows: Reader, case: str) -> Vignette:
    if rows.kind(case) != "case":
        raise LookupError(f"{case} is not a stored case")
    vignette = resolve(rows.get, case, Case).vignette
    if (held := holder(rows, vignette)) is not None:
        other, same_id = held
        change = "new content" if same_id else "a new name"
        raise ValueError(
            f"{case} gives family {other}'s vignette {change}; rebind the family"
        )
    return vignette


def survey(rows: Reader, source: str, listing: Mapping[str, Fingerprint]) -> Survey:
    latest_bindings = latest((b.family, b) for b in rows.bindings_in(source))
    current = [latest_bindings[f] for f in sorted(latest_bindings)]
    by_id = {b.vignette.id: b for b in current}
    unchanged: list[int] = []
    changed: list[tuple[int, str, Fingerprint]] = []
    renamed: list[tuple[int, str, str]] = []
    new: list[str] = []
    for id, fingerprint in sorted(listing.items()):
        moved = [
            b
            for b in current
            if b.vignette.fingerprint == fingerprint
            and b.vignette.id != id
            and b.vignette.id not in listing
        ]
        if (b := by_id.get(id)) is not None:
            if b.vignette.fingerprint == fingerprint:
                unchanged.append(b.family)
            else:
                changed.append((b.family, id, fingerprint))
        elif len(moved) == 1:
            renamed.append((moved[0].family, moved[0].vignette.id, id))
        else:
            new.append(id)
    seen = {*unchanged, *(f for f, *_ in changed), *(f for f, *_ in renamed)}
    return Survey(
        unchanged=tuple(unchanged),
        changed=tuple(changed),
        renamed=tuple(renamed),
        new=tuple(new),
        gone=tuple(b.family for b in current if b.family not in seen),
    )


def rebind_appendix(appendix: Appendix, vignette: Vignette) -> Appendix:
    return Appendix(vignette=vignette, text=appendix.text)


def rebind_case(
    case: Case, vignette: Vignette, get: Resolver
) -> tuple[Case, dict[str, Appendix]]:
    appendices = {
        a: rebind_appendix(resolve(get, a, Appendix), vignette) for a in case.appendices
    }
    rebound = tuple(appendices[a].digest for a in case.appendices)
    return Case(vignette=vignette, appendices=rebound), appendices


def plan_repair(
    rows: Reader,
    family: int,
    *,
    id: str | None = None,
    fingerprint: Fingerprint | None = None,
) -> RepairPlan:
    old = bindings(rows, family)[-1].vignette
    vignette = Vignette(
        source=old.source,
        id=old.id if id is None else id,
        fingerprint=old.fingerprint if fingerprint is None else fingerprint,
    )
    if (vignette.id == old.id) == (vignette.fingerprint == old.fingerprint):
        raise ValueError("a repair changes exactly one of the id or the fingerprint")
    if (held := holder(rows, vignette, but=family)) is not None:
        other, same_id = held
        taken = f"the id {vignette.id!r}" if same_id else "that content"
        raise ValueError(f"family {other} already has {taken}")
    registry = Registry()
    appendices: dict[str, str] = {}

    def rebind(appendix: str) -> str:
        if appendix not in appendices:
            moved = rebind_appendix(resolve(rows.get, appendix, Appendix), vignette)
            appendices[appendix] = registry.add(moved)
        return appendices[appendix]

    cases: dict[str, str] = {}
    for digest in rows.bound_to(old, "case"):
        case, moved = rebind_case(resolve(rows.get, digest, Case), vignette, rows.get)
        appendices |= {a: registry.add(m) for a, m in moved.items()}
        cases[digest] = registry.add(case)
    moves = [
        (t, rebind(h.digest))
        for t, h in heads_holding(rows, rows.bound_to(old, "appendix")).items()
    ]
    if id is not None:
        for t, h in heads_holding(rows, rows.expectations(list(cases))).items():
            e = resolve(rows.get, h.digest, Expectation)
            _ = registry.add(rows.get(e.expectation_schema))
            rekeyed = Expectation(
                case=cases[e.case], expectation_schema=e.expectation_schema, data=e.data
            )
            moves.append((t, registry.add(rekeyed)))
    return RepairPlan(
        vignette=vignette,
        registry=registry,
        cases=cases,
        appendices=appendices,
        moves=moves,
    )


def stale_cases(rows: Reader, refs: list[tuple[str, str]]) -> list[Behind]:
    stale: list[Behind] = []
    for path, digest in refs:
        if (family := family_of(rows, digest)) is None:
            continue
        current = bindings(rows, family)[-1]
        case = resolve(rows.get, digest, Case)
        if (
            case.vignette == current.vignette
            or about(rows, Subject(family=family)).deleted
        ):
            continue
        replacement, _ = rebind_case(case, current.vignette, rows.get)
        stale.append(
            Behind(
                path=path,
                digest=digest,
                binding=current,
                replacement=replacement.digest,
            )
        )
    return stale
