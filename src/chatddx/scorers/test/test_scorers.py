import json
from typing import Any, cast

import pytest
from jsonschema import validators
from pydantic import JsonValue

from chatddx.core.rig import entry, rig
from chatddx.factors.base import Code, resolve
from chatddx.factors.bundle import Registry
from chatddx.factors.request import Skeleton, SlotName
from chatddx.factors.scoring import Judge, Scorer, View
from chatddx.factors.select import reaches
from chatddx.factors.test.sample import world
from chatddx.facts.facts import Facts
from chatddx.fake_vllm.chat import ANSWER as ANSWER_TEXT, instance
from chatddx.scorers.aggregate import AGGREGATES
from chatddx.scorers.patterns import (
    Pattern,
    first_mention,
    mentions,
    reciprocal_rank,
    score as patterns,
)
from chatddx.scorers.scorer import Scored, function, score
from chatddx.seed import load_cases, plan_factors
from chatddx.seed.plan import SAMPLE

SCHEMA = "sha256:" + "0" * 64
PATTERNS = "chatddx.scorers.patterns:score"


@pytest.mark.parametrize(
    "pattern, text, at",
    [
        ("pneumonia", "Likely pneumonia", 7),
        ("PNEUMONIA", "pneumonia", 0),
        ("mi", "Iron deficiency anemia", None),
        ("mi", "Acute MI", 6),
        ("hus", "Thus far unexplained fever", None),
        ("stone", "Kidney stones", None),
        ("stone*", "Kidney stones", 7),
        ("meningit*", "Viral meningitis", 6),
        ("tumör", "Hjärntumör", None),
        ("hjärntumör", "Misstänkt hjärntumör", 10),
        ("acute coronary syndrome", "Acute coronary syndrome (ACS)", 0),
        ("coronary acute", "Acute coronary syndrome", None),
        ("parkinson's disease", "Parkinson's disease", 0),
        ("gastro & intestinal", "Gastro-intestinal bleed", 0),
        ("gastro & intestinal", "Gastrointestinal bleed", None),
        ("copd | exacerbation & pulmonary", "Pulmonary exacerbation", 0),
        ("copd | exacerbation & pulmonary", "An exacerbation", None),
        ("copd | exacerbation & pulmonary", "Known COPD", 6),
        ("(renal | kidney) & (colic | stone*)", "Renal colic", 0),
        ("(renal | kidney) & (colic | stone*)", "Kidney stones", 0),
        ("(renal | kidney) & (colic | stone*)", "Renal failure", None),
        ("liver & failure", "Failure of the liver", 0),
    ],
)
def test_a_pattern_finds_whole_words(pattern: str, text: str, at: int | None) -> None:
    assert Pattern(pattern).find(text) == at


@pytest.mark.parametrize(
    "pattern, problem",
    [
        ("", "at least one word"),
        ("(pneumonia | copd", "never closed"),
        ("pneumonia &", "a word is missing"),
        ("pneumonia ) copd", r"unexpected '\)'"),
        ("* | copd", "names no word"),
    ],
)
def test_a_pattern_that_doesn_t_read_is_refused(pattern: str, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        _ = Pattern(pattern)


def test_every_sample_target_reads() -> None:
    for case in load_cases(SAMPLE / "cases.toml").values():
        assert isinstance(case.targets, dict)
        for kind, target in case.targets.items():
            if kind == "notes":
                continue
            assert isinstance(target, dict) and isinstance(target["pattern"], str)
            _ = Pattern(target["pattern"])


def test_the_reciprocal_rank_is_that_of_the_first_diagnosis_found() -> None:
    differential = ["Pulmonary embolism", "Pneumonia", "Pneumonia, atypical"]
    assert reciprocal_rank(differential, "pneumonia") == Scored(
        0.5, {"answer": "2. Pneumonia"}
    )
    assert reciprocal_rank(differential, "sepsis") == Scored(
        0.0, {"reason": "not listed"}
    )
    assert reciprocal_rank(None, "pneumonia") == Scored(0.0, {"reason": "no answer"})


@pytest.mark.parametrize(
    "target, differential",
    [
        (
            "myocardial & infarction | mi | acs | acute & coronary & syndrome",
            ["Iron deficiency anemia"],
        ),
        ("uti | urinary & infection* | cystitis | urosepsis", ["Acute cholecystitis"]),
        ("hemolytic & uremic & syndrome | hus", ["Thus far unexplained fever"]),
        ("liver & failure | cirrosis | cirrhosis", ["Heart failure", "Liver abscess"]),
        ("overdose | drug & use | intoxication", ["Pain because of a fall"]),
        ("(gastro & intestinal | gi) & inflammation", ["Allergic inflammation"]),
    ],
)
def test_what_the_old_scorer_found_in_parts_of_words_is_not_found(
    target: str, differential: list[str]
) -> None:
    assert reciprocal_rank(differential, target).value == 0


def test_the_first_mention_is_counted_in_characters_to_its_first_word() -> None:
    text = "Cough and fever. Pneumonia is likely.\nPE is less likely."
    assert first_mention([text], "pneumonia") == Scored(
        17.0, {"answer": "Pneumonia is likely."}
    )
    assert first_mention([text], "pe | embolism") == Scored(
        38.0, {"answer": "PE is less likely."}
    )
    assert first_mention([text], "sepsis") == Scored(None, {"reason": "never named"})
    assert first_mention(None, "sepsis") == Scored(None, {"reason": "no answer"})


def test_a_first_mention_holds_the_target_to_one_sentence_at_a_time() -> None:
    text = "Heart failure is likely. A liver abscess is not."
    assert first_mention([text], "liver & failure").value is None
    assert first_mention([text], "liver & abscess").value == 27


def test_mentions_says_whether_the_target_is_named_at_all() -> None:
    assert mentions(["Admit to the surgical ward."], "admit*") == Scored(
        1.0, {"answer": "Admit to the surgical ward."}
    )
    assert mentions(["Discharge home."], "admit*") == Scored(
        0.0, {"reason": "not named"}
    )
    assert mentions([], "admit*") == Scored(0.0, {"reason": "not named"})
    assert mentions(None, "admit*") == Scored(0.0, {"reason": "no answer"})


def test_mentions_with_no_target_expects_nothing_named() -> None:
    assert mentions([], None) == Scored(1.0, {"reason": "none named, as expected"})
    assert mentions(["Signs of sepsis."], None) == Scored(
        0.0, {"answer": "Signs of sepsis.", "reason": "none expected"}
    )
    assert mentions(None, None) == Scored(0.0, {"reason": "no answer"})


@pytest.mark.parametrize(
    "aggregate, values, expected",
    [
        ("mean", [1.0, 0.5, 0.0], 0.5),
        ("mean", [], 0.0),
        ("std", [1.0, 0.0], 0.7071),
        ("var", [1.0, 0.0], 0.5),
        ("stderr", [1.0, 0.0], 0.5),
        ("stderr", [1.0], 0.0),
        ("std", [], 0.0),
    ],
)
def test_each_aggregate_is_inspect_s(
    aggregate: str, values: list[float], expected: float
) -> None:
    assert AGGREGATES[aggregate](values) == pytest.approx(expected, abs=1e-4)


# ------------------------------------------------------------------ interface


def plan_scorer(**changes: Any) -> Scorer:
    fields: dict[str, Any] = {
        "code": rig(),
        "entry_point": PATTERNS,
        "expectation_schema": SCHEMA,
        "views": (
            View(
                output="$.diagnoses[*].diagnosis",
                expectation="/diagnosis",
                metric="reciprocal_rank",
            ),
            View(output="$.acute_warning", expectation="/warning", metric="mentions"),
            View(
                output="$.management.disposition",
                expectation="/disposition",
                metric="mentions",
            ),
        ),
    }
    return Scorer.model_validate(fields | changes)


ANSWER: JsonValue = {
    "diagnoses": [{"diagnosis": "Pulmonary embolism"}, {"diagnosis": "Pneumonia"}],
    "acute_warning": None,
    "management": {"disposition": "Admit to the medical ward"},
}
TARGETS: JsonValue = {
    "diagnosis": {"pattern": "pneumonia"},
    "warning": {"pattern": "sepsis"},
    "disposition": {"pattern": "admit*"},
}


def test_a_scorer_scores_each_of_its_views_through_its_entry_point() -> None:
    scorer = plan_scorer()
    run = function(scorer)
    assert run is patterns
    assert score(scorer, run, ANSWER, TARGETS) == [
        Scored(0.5, {"answer": "2. Pneumonia"}),
        Scored(0.0, {"reason": "not named"}),
        Scored(1.0, {"answer": "Admit to the medical ward"}),
    ]
    assert [s.value for s in score(scorer, run, None, TARGETS)] == [0.0, 0.0, 0.0]


def test_a_view_without_a_target_or_a_readable_one_isn_t_scored() -> None:
    scorer = plan_scorer()
    targets: JsonValue = {"diagnosis": {"pattern": "pneumonia &"}, "warning": False}
    first, warning, disposition = score(scorer, function(scorer), ANSWER, targets)
    assert first.value is None
    assert first.detail == {
        "reason": "the pattern doesn't read: a word is missing in 'pneumonia &'"
    }
    assert warning == Scored(1.0, {"reason": "none named, as expected"})
    assert disposition == Scored(None, {"reason": "no target"})


def test_free_text_is_split_by_the_view_before_it_is_scored() -> None:
    scorer = plan_scorer(
        views=(
            View(expectation="/diagnosis", metric="first_mention"),
            View(split="lines@1", expectation="/diagnosis", metric="reciprocal_rank"),
        )
    )
    text = "1. Pulmonary embolism\n2. Pneumonia"
    assert [s.value for s in score(scorer, function(scorer), text, TARGETS)] == [
        25.0,
        0.5,
    ]


def test_only_the_running_code_and_its_metrics_are_run() -> None:
    other = Code(distribution="chatddx", version="0.0.1", revision=None)
    with pytest.raises(LookupError, match="pins chatddx 0.0.1, but chatddx"):
        _ = function(plan_scorer(code=other))
    with pytest.raises(LookupError, match="names nothing"):
        _ = function(plan_scorer(entry_point="chatddx.scorers.patterns:absent"))
    with pytest.raises(TypeError, match="isn't a function"):
        _ = function(plan_scorer(entry_point="chatddx.scorers.patterns:_WORD"))
    unknown = (View(metric="recall"),)
    with pytest.raises(LookupError, match="knows no metric recall"):
        _ = function(plan_scorer(views=unknown))
    assert entry(rig(), PATTERNS) is patterns


def test_a_judged_view_can_t_be_scored_yet() -> None:
    scorer = plan_scorer(views=(View(metric="mentions", judge="sha256:" + "1" * 64),))
    with pytest.raises(NotImplementedError, match="need a judge"):
        _ = score(scorer, function(scorer), ANSWER, TARGETS)


# ------------------------------------------------------------------ the sample

TARGETS_SCHEMA = json.loads((SAMPLE / "schemas" / "targets.json").read_text())


def sample_scorer(name: str) -> tuple[Scorer, tuple[str | None, ...]]:
    plan = plan_factors(
        SAMPLE / "factors.toml", Facts.load(SAMPLE / "facts.toml"), rig()
    )
    record = plan.named("scorer", name)
    scorer = plan.registry.get(record.digest)
    assert isinstance(scorer, Scorer)
    return scorer, record.labels


def scored(name: str, answer: JsonValue, targets: JsonValue) -> dict[str, Scored]:
    scorer, labels = sample_scorer(name)
    values = score(scorer, function(scorer), answer, targets)
    return {str(label): value for label, value in zip(labels, values, strict=True)}


def test_the_targets_schema_takes_the_old_chatddx_s_target_kinds() -> None:
    valid = validators.validator_for(TARGETS_SCHEMA)(TARGETS_SCHEMA).is_valid
    pneumonia: JsonValue = {"pattern": "pneumonia"}
    assert valid({"diagnosis": pneumonia})
    assert valid({"diagnosis": {"text": "Community-acquired pneumonia"}})
    assert valid(
        {
            "diagnosis": {"text": "Pneumonia", "pattern": "pneumonia"},
            "warning": False,
            "dont_miss": {"pattern": "pulmonary embolism"},
        }
    )
    invalid_targets: list[JsonValue] = [
        {"diagnosis": False},
        {"diagnosis": pneumonia, "dont_miss": False},
        {"diagnosis": {}},
        {"diagnosis": {"pattern": ""}},
        {"diagnosis": pneumonia, "critical": pneumonia},
    ]
    for invalid in invalid_targets:
        assert not valid(invalid), invalid


def test_a_case_s_notes_keep_their_author_language_comment_and_answer() -> None:
    valid = validators.validator_for(TARGETS_SCHEMA)(TARGETS_SCHEMA).is_valid
    note: dict[str, JsonValue] = {
        "author": "Olof",
        "language": "sv",
        "comment": "Chock.",
    }
    pneumonia: JsonValue = {"pattern": "pneumonia"}
    assert valid({"diagnosis": pneumonia, "notes": [note]})
    assert valid({"diagnosis": pneumonia, "notes": [note | {"answer": "Rundodla."}]})
    invalid_notes: list[JsonValue] = [
        [],
        [{k: v for k, v in note.items() if k != "author"}],
        [note | {"language": "Swedish"}],
        [note | {"comment": ""}],
        [note | {"answer": ""}],
        [note | {"date": "2026-09-28"}],
    ]
    for notes in invalid_notes:
        assert not valid({"diagnosis": pneumonia, "notes": notes}), notes


def test_the_sample_holds_olof_s_notes_on_three_cases_as_written() -> None:
    cases = load_cases(SAMPLE / "cases.toml")
    valid = validators.validator_for(TARGETS_SCHEMA)(TARGETS_SCHEMA).is_valid
    assert all(valid(case.targets) for case in cases.values())
    noted = {
        id: case.targets["notes"]
        for id, case in cases.items()
        if isinstance(case.targets, dict) and "notes" in case.targets
    }
    assert sorted(noted) == ["Dutchfall11w", "Dutchfall1w", "casesfromedn1"]
    for id, notes in noted.items():
        assert isinstance(notes, list) and len(notes) == 1
        [note] = notes
        assert isinstance(note, dict)
        assert (note["author"], note["language"]) == ("Olof", "sv")
        assert ("answer" in note) == (id != "Dutchfall1w")
    [eleven] = cast(list[dict[str, str]], noted["Dutchfall11w"])
    assert eleven["comment"].startswith("identifiera tecken på prechock eller chock")
    assert eleven["answer"].endswith("påbörja empirisk bredspektrumantibiotika.")


def test_a_judge_reads_a_case_s_notes_beside_the_targets_the_patterns_read() -> None:
    targets = load_cases(SAMPLE / "cases.toml")["Dutchfall11w"].targets
    answer: JsonValue = {
        "diagnoses": [
            {"diagnosis": "Septic shock", "critical": True},
            {"diagnosis": "Ruptured abdominal aortic aneurysm", "critical": True},
        ],
        "acute_warning": "Signs of shock",
        "management": {"disposition": "Intensive care"},
    }
    assert scored("plan", answer, targets)["differential"] == Scored(
        0.5, {"answer": "2. Ruptured abdominal aortic aneurysm"}
    )
    olof = "$.notes[?@.author == 'Olof']"
    assert reaches(TARGETS_SCHEMA, f"{olof}.comment")
    reg = Registry()
    ids = world(reg)
    judge = resolve(reg.get, ids["judge"], Judge)

    def fills(expectation: str) -> dict[SlotName, str]:
        view = View(
            output="$.diagnoses[*].diagnosis",
            expectation=expectation,
            metric="judge",
            judge=ids["judge"],
        )
        return judge.fills(view, answer, targets)

    [note] = cast(dict[str, list[dict[str, str]]], targets)["notes"]
    assert fills(f"{olof}.comment") == {
        "completion": "Septic shock\nRuptured abdominal aortic aneurysm",
        "expectation": note["comment"],
    }
    assert fills(f"{olof}.answer")["expectation"] == note["answer"]


def test_a_dont_miss_is_held_to_what_each_output_can_say() -> None:
    targets: JsonValue = {
        "diagnosis": {"pattern": "pneumonia"},
        "dont_miss": {"text": "Pulmonary embolism", "pattern": "pulmonary embolism"},
    }

    def plan(critical: bool) -> JsonValue:
        return {
            "diagnoses": [
                {"diagnosis": "Pneumonia", "critical": False},
                {"diagnosis": "Pulmonary embolism", "critical": critical},
            ],
            "acute_warning": None,
            "management": {"disposition": "Admit"},
        }

    assert scored("plan", plan(critical=True), targets)["dont-miss"] == Scored(
        1.0, {"answer": "Pulmonary embolism"}
    )
    named = scored("plan", plan(critical=False), targets)
    assert named["dont-miss"] == Scored(0.0, {"reason": "not named"})
    assert named["differential"] == Scored(1.0, {"answer": "1. Pneumonia"})
    listed: JsonValue = {"diagnoses": ["Pneumonia", "Pulmonary embolism"]}
    assert scored("diagnoses", listed, targets)["dont-miss"].value == 1.0
    lines = "1. Pneumonia\n2. Pulmonary embolism"
    assert scored("free-text", lines, targets)["dont-miss"].value == 1.0
    assert scored("free-text", "1. Pneumonia", targets)["dont-miss"].value == 0.0
    no_dont_miss: JsonValue = {"diagnosis": {"pattern": "pneumonia"}}
    assert scored("plan", plan(critical=True), no_dont_miss)["dont-miss"] == Scored(
        None, {"reason": "no target"}
    )


def test_a_case_that_calls_for_no_warning_is_met_by_a_plan_that_raises_none() -> None:
    targets: JsonValue = {"diagnosis": {"pattern": "pneumonia"}, "warning": False}
    plan: dict[str, JsonValue] = {
        "diagnoses": [{"diagnosis": "Pneumonia", "critical": False}],
        "acute_warning": None,
        "management": {"disposition": "Home"},
    }
    assert scored("plan", plan, targets)["warning"] == Scored(
        1.0, {"reason": "none named, as expected"}
    )
    raised = plan | {"acute_warning": "Signs of sepsis"}
    assert scored("plan", raised, targets)["warning"] == Scored(
        0.0, {"answer": "Signs of sepsis", "reason": "none expected"}
    )


def test_a_target_s_words_are_a_judge_s_and_its_pattern_the_pattern_scorers() -> None:
    words: JsonValue = {"diagnosis": {"text": "Community-acquired pneumonia"}}
    answer: JsonValue = {
        "diagnoses": [{"diagnosis": "Pulmonary embolism"}, {"diagnosis": "Pneumonia"}],
        "acute_warning": None,
        "management": {"disposition": "Admit"},
    }
    assert scored("plan", answer, words)["differential"] == Scored(
        None, {"reason": "the target has words, for a judge, but no pattern"}
    )
    assert reaches(TARGETS_SCHEMA, "/diagnosis/text")
    reg = Registry()
    ids = world(reg)
    judge = resolve(reg.get, ids["judge"], Judge)
    view = View(
        output="$.diagnoses[*].diagnosis",
        expectation="/diagnosis/text",
        metric="judge",
        judge=ids["judge"],
    )
    assert judge.fills(view, answer, words) == {
        "completion": "Pulmonary embolism\nPneumonia",
        "expectation": "Community-acquired pneumonia",
    }


# Which scorer reads each configuration's output, by the output's shape.
READS = {
    "plan": "plan",
    "plan-shown": "plan",
    "plan-prompted": "plan",
    "plan-web": "plan",
    "diagnoses": "diagnoses",
    "diagnoses-tool": "diagnoses",
    "free-text": "free-text",
}


def test_each_sample_scorer_s_views_pick_from_the_answers_it_reads() -> None:
    plan = plan_factors(
        SAMPLE / "factors.toml", Facts.load(SAMPLE / "facts.toml"), rig()
    )
    read: set[str] = set()
    for record in plan.records:
        if record.kind != "skeleton":
            continue
        skeleton = plan.registry.get(record.digest)
        assert isinstance(skeleton, Skeleton)
        configuration = record.name.partition(" (")[0]
        scorer = plan.registry.get(plan.named("scorer", READS[configuration]).digest)
        assert isinstance(scorer, Scorer)
        schema = skeleton.output_schema
        answer: JsonValue = ANSWER_TEXT if schema is None else instance(schema)
        # The fake writes every boolean false; the plan's dont-miss view reads only the
        # diagnoses an answer marks critical.
        if isinstance(answer, dict) and isinstance(answer.get("diagnoses"), list):
            for item in cast(list[JsonValue], answer["diagnoses"]):
                if isinstance(item, dict) and "critical" in item:
                    item["critical"] = True
        for view in scorer.views:
            assert view.output_items(answer), (record.name, view.output)
        values = score(scorer, function(scorer), answer, TARGETS)
        assert len(values) == len(scorer.views)
        read.add(READS[configuration])
    assert read == {"plan", "diagnoses", "free-text"}
