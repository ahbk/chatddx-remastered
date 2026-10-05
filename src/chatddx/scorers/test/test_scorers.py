from typing import Any

import pytest
from pydantic import JsonValue

from chatddx.core.rig import entry, rig
from chatddx.factors.base import Code
from chatddx.factors.request import Skeleton
from chatddx.factors.scoring import Scorer, View
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
        for target in case.targets.values():
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
        for view in scorer.views:
            assert view.output_items(answer), (record.name, view.output)
        values = score(scorer, function(scorer), answer, TARGETS)
        assert len(values) == len(scorer.views)
        read.add(READS[configuration])
    assert read == {"plan", "diagnoses", "free-text"}
