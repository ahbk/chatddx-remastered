import pytest

from chatddx.catalog import Entry, EntryField, Subject, language
from chatddx.catalog.test.memory import Memory, compiled
from chatddx.factors.base import resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.cases import Case
from chatddx.factors.request import Prompt, Slot, Translations, texts
from chatddx.factors.scoring import Judge, Scoring
from chatddx.factors.test.sample import generation_recipe, world


def test_a_request_is_in_the_language_its_parts_agree_on() -> None:
    reg = Registry()
    ids = world(reg)
    rows = Memory(reg)
    base = generation_recipe(reg)
    plan = compiled(reg, base)
    assert base.instructions is not None
    for part in (base.instructions, base.prompt):
        rows.set_language(part, "en")
    assert language.language_of(rows, plan.skeleton) is None
    rows.set_language(base.output, "en")
    assert language.language_of(rows, plan.skeleton) == "en"
    assert language.language_of(rows, ids["trial"]) == "en"

    needed = [t for part in texts(base, reg.get).values() for t in part]
    swedish = reg.add(Translations(entries={t: f"[sv] {t}" for t in needed}))
    translated = compiled(reg, base.model_copy(update={"translations": swedish}))
    assert language.language_of(rows, translated.skeleton) is None
    rows.set_language(swedish, "sv")
    assert language.language_of(rows, translated.skeleton) == "sv"


def test_every_compilation_of_a_skeleton_votes() -> None:
    reg = Registry()
    rows = Memory(reg)
    base = generation_recipe(reg)
    plan = compiled(reg, base)
    assert base.instructions is not None
    for part in (base.instructions, base.prompt, base.output):
        rows.set_language(part, "en")
    vignette, appendices = Slot(slot="vignette"), Slot(slot="appendices")
    split = reg.add(
        Prompt(segments=("Case:", "\n", vignette, appendices, "\n\nDifferential?"))
    )
    assert compiled(reg, base.model_copy(update={"prompt": split})).skeleton == (
        plan.skeleton
    )
    assert language.language_of(rows, plan.skeleton) == "en"
    needed = [t for part in texts(base, reg.get).values() for t in part]
    unchanged = reg.add(Translations(entries={t: t for t in needed}))
    retold = compiled(reg, base.model_copy(update={"translations": unchanged}))
    assert retold.skeleton == plan.skeleton
    rows.set_language(unchanged, "sv")
    assert language.language_of(rows, plan.skeleton) is None


def test_judges_follow_their_skeleton_and_cases_their_family() -> None:
    reg = Registry()
    ids = world(reg)
    rows = Memory(reg)
    rows.set_language(resolve(reg.get, ids["judge"], Judge).skeleton, "en")
    assert language.language_of(rows, ids["judge"]) == "en"

    expectation = resolve(reg.get, ids["scoring"], Scoring).expectations[0]
    assert language.language_of(rows, ids["case"]) is None
    _ = rows.bind(1, resolve(reg.get, ids["case"], Case).vignette)
    rows.note(Subject(family=1), Entry(field=EntryField.LANGUAGE, value="sv"))
    assert language.language_of(rows, ids["case"]) == "sv"
    assert language.language_of(rows, expectation) == "sv"


def test_languages_are_refused_where_they_are_not_read() -> None:
    reg = Registry()
    ids = world(reg)
    rows = Memory(reg)
    base = generation_recipe(reg)
    plan = compiled(reg, base)
    assert language.check_language(rows, base.prompt, "sv") == "chunk.prompt"
    with pytest.raises(ValueError, match="not a language tag"):
        _ = language.check_language(rows, base.prompt, "Swedish")
    with pytest.raises(ValueError, match="trial components have no language"):
        _ = language.check_language(rows, ids["trial"], "sv")
    with pytest.raises(ValueError, match="comes from its recipe"):
        _ = language.check_language(rows, plan.skeleton, "sv")
    with pytest.raises(LookupError):
        _ = language.check_language(rows, "sha256:" + "9" * 64, "sv")

    swedish = Entry(field=EntryField.LANGUAGE, value="sv")
    language.check_entry(Subject(family=1), swedish)
    with pytest.raises(ValueError, match="kept on its digest"):
        language.check_entry(Subject(thread=1), swedish)
