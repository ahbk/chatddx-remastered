import pytest
from pydantic import JsonValue, ValidationError

from chatddx.factors.scoring import View
from chatddx.factors.select import parse_selector, reaches, select, split

PLAN: JsonValue = {
    "diagnoses": [
        {"diagnosis": "ACS", "critical": True},
        {"diagnosis": "GERD", "critical": False},
        {"diagnosis": "PE"},
    ],
    "acute_warning": None,
    "management": {"disposition": "admit"},
}


def test_jsonpath_subset_selects_as_rfc_9535() -> None:
    assert select(PLAN, "$.diagnoses[*].diagnosis") == ["ACS", "GERD", "PE"]
    assert select(PLAN, "$.diagnoses[?@.critical == true].diagnosis") == ["ACS"]
    assert select(PLAN, "$.diagnoses[?@.critical].diagnosis") == ["ACS", "GERD"]
    assert select(PLAN, "$.diagnoses[?@.critical != true].diagnosis") == ["GERD", "PE"]
    assert select(PLAN, "$.acute_warning") == [None]
    assert select(PLAN, "$.management.disposition") == ["admit"]
    assert select(PLAN, "$['management'][\"disposition\"]") == ["admit"]
    assert select(PLAN, "$.diagnoses[-1].diagnosis") == ["PE"]
    assert select(PLAN, "$.diagnoses[5]") == []
    assert select(PLAN, "$.missing.deeper") == []
    assert select(PLAN, "$.management.*") == ["admit"]
    assert select(PLAN, "$") == [PLAN]
    assert select({"a": 1, "b": 2}, "$[?@ == 2]") == [2]
    assert select([1, 1.0, True, "1"], "$[?@ == 1]") == [1, 1.0]
    assert select(["it's", "its"], "$[?@ == 'it\\'s']") == ["it's"]
    assert select([{"a": {"b": None}}, {"a": {}}], "$[?@.a.b == null]") == [
        {"a": {"b": None}}
    ]


def test_json_pointers_still_select() -> None:
    assert select(PLAN, "") == [PLAN]
    assert select(PLAN, "/management/disposition") == ["admit"]
    assert select(PLAN, "/diagnoses/0/diagnosis") == ["ACS"]
    assert select(PLAN, "/diagnoses/01") == []
    assert select({"a/b": 1, "c~d": 2}, "/a~1b") == [1]
    assert select({"a/b": 1, "c~d": 2}, "/c~0d") == [2]


@pytest.mark.parametrize(
    "selector",
    [
        "$..diagnosis",
        "$.diagnoses[0:2]",
        "$[?@.a > 1]",
        "$.a b",
        "diagnoses",
        "$[?@.a == ]",
        "$['unclosed]",
        "/a~2",
    ],
)
def test_selectors_outside_the_subset_are_refused(selector: str) -> None:
    with pytest.raises(ValueError):
        _ = parse_selector(selector)
    with pytest.raises(ValidationError):
        _ = View(output=selector, metric="m")


def test_free_text_splits_into_lines() -> None:
    text = "1. ACS\n2) GERD\n\n- PE\n• AAA\n* Pneumonia\n   \nSepsis"
    assert split("lines@1", [text, 3]) == [
        "ACS",
        "GERD",
        "PE",
        "AAA",
        "Pneumonia",
        "Sepsis",
        3,
    ]
    view = View(split="lines@1", metric="m")
    assert view.output_items("1. ACS\n2. PE") == ["ACS", "PE"]
    assert View(metric="m").output_items("1. ACS\n2. PE") == ["1. ACS\n2. PE"]
    assert View(expectation="/ddx", metric="m").expectation_items({"ddx": ["ACS"]}) == [
        ["ACS"]
    ]


def test_selectors_are_checked_against_schemas() -> None:
    schema: JsonValue = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "diagnoses": {"type": "array", "items": {"$ref": "#/$defs/d"}},
            "management": {
                "type": "object",
                "properties": {"disposition": {"type": "string"}},
            },
            "either": {"anyOf": [{"type": "string"}, {"type": "array"}]},
        },
        "$defs": {
            "d": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "diagnosis": {"type": "string"},
                    "critical": {"type": "boolean"},
                },
            }
        },
    }
    assert reaches(schema, "$.diagnoses[*].diagnosis")
    assert reaches(schema, "$.diagnoses[?@.critical == true].diagnosis")
    assert not reaches(schema, "$.diagnoses[?@.urgent].diagnosis")
    assert not reaches(schema, "$.diagnoses[*].urgent")
    assert reaches(schema, "$.management.disposition")
    assert reaches(schema, "$.management.anything")
    assert not reaches(schema, "$.management.disposition.deeper")
    assert reaches(schema, "/diagnoses/0/diagnosis")
    assert not reaches(schema, "$.acute_warning")
    assert reaches(schema, "$.either[0]")
    assert not reaches(schema, "$.diagnoses.diagnosis")
    assert reaches({}, "$.anything[*].goes")
    assert reaches(None, "")
    assert not reaches(None, "$.diagnoses")
    assert reaches(
        {"type": "object", "properties": {"a": {"$ref": "other.json"}}}, "$.a.b"
    )
