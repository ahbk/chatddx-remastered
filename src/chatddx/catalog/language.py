from chatddx.factors.base import resolve
from chatddx.factors.request import Recipe, texts
from chatddx.factors.scoring import Expectation, Judge
from chatddx.factors.trial import Trial

from .families import family_of
from .model import LANGUAGE_KINDS, Entry, EntryField, Subject, language_tag, latest
from .read import Reader, about


def own(rows: Reader, digest: str) -> str | None:
    return latest(rows.languages([digest])).get(digest)


def language_of(rows: Reader, digest: str) -> str | None:
    match rows.kind(digest):
        case "case":
            family = family_of(rows, digest)
            if family is None:
                return None
            return about(rows, Subject(family=family)).language
        case "expectation":
            return language_of(rows, resolve(rows.get, digest, Expectation).case)
        case "trial":
            return language_of(rows, resolve(rows.get, digest, Trial).skeleton)
        case "judge":
            return language_of(rows, resolve(rows.get, digest, Judge).skeleton)
        case "skeleton":
            compilations = rows.compilations(digest)
            if not compilations:
                return own(rows, digest)
            known = {
                language
                for c in compilations
                if (language := _recipe_language(rows, c.recipe)) is not None
            }
            return known.pop() if len(known) == 1 else None
        case _:
            return own(rows, digest)


# src/chatddx/store/migrations/0024-t2-catalog-languages.sql repeats these checks.
def check_language(rows: Reader, digest: str, value: str) -> str:
    kind = rows.kind(digest)
    if kind is None:
        raise LookupError(f"{digest} is not in the store")
    if kind not in LANGUAGE_KINDS:
        raise ValueError(f"{kind} components have no language of their own")
    if kind == "skeleton" and rows.compilations(digest):
        raise ValueError(
            f"{digest}: a compiled skeleton's language comes from its recipe"
        )
    _ = language_tag(value)
    return kind


# src/chatddx/store/migrations/0024-t2-catalog-languages.sql repeats this check.
def check_entry(subject: Subject, entry: Entry) -> None:
    if entry.field == EntryField.LANGUAGE and subject.family is None:
        raise ValueError(
            "a language entry is for a family's vignette; "
            + "a component's language is kept on its digest"
        )


def _recipe_language(rows: Reader, recipe: Recipe) -> str | None:
    if recipe.translations is not None:
        return own(rows, recipe.translations)
    parts: list[str | None] = [
        getattr(recipe, part)
        for part in texts(recipe, rows.get)
        if part != "appendix_layout"
    ]
    languages = {None if p is None else own(rows, p) for p in parts}
    return languages.pop() if len(languages) == 1 else None
