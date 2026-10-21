import copy

import pytest
from pydantic import ValidationError

from tests.conftest import load_spec_dict
from xlagent.spec import RequirementsSpec, SpecSubmission, spec_json_schema


@pytest.fixture
def spec() -> dict:
    return copy.deepcopy(load_spec_dict()["spec"])


def test_valid_spec_round_trips(spec):
    parsed = RequirementsSpec.model_validate(spec)
    assert parsed.output_format.file_name == "sales_summary.xlsx"
    assert RequirementsSpec.model_validate(parsed.model_dump()) == parsed


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda s: s.update(inputs=[]), "inputs"),
        (lambda s: s.update(steps=[]), "steps"),
        (lambda s: s["output_format"]["sheets"][0].update(name="Q1/Q2"), "Excel does not allow"),
        (lambda s: s["output_format"]["sheets"][0].update(name="x" * 32), "31"),
        (lambda s: s["output_format"].update(file_name="summary.csv"), "must end with .xlsx"),
        (lambda s: s["output_format"].update(file_type="csv", file_name="a.csv"), "one sheet"),
        (lambda s: s["output_format"]["sheets"][1].update(name="summary"), "unique"),
        (lambda s: s["output_format"]["sheets"][0].update(sort_by=["Nope"]), "unknown columns"),
        (lambda s: s["output_format"]["sheets"][0]["columns"].append("Shop"), "duplicate"),
        (lambda s: s["schedule"].update(frequency="sometimes"), "frequency"),
        (lambda s: s.update(business_rules=["ok", "  "]), "blank"),
        (lambda s: s.update(surprise="field"), "surprise"),
    ],
)
def test_invalid_specs_are_rejected(spec, mutate, message):
    mutate(spec)
    with pytest.raises(ValidationError) as exc:
        RequirementsSpec.model_validate(spec)
    assert message in str(exc.value)


def test_submission_defaults_to_no_assumptions(spec):
    sub = SpecSubmission.model_validate({"spec": spec})
    assert sub.assumptions == []


def test_tool_schema_exposes_all_spec_sections():
    schema = spec_json_schema()
    spec_def = schema["$defs"]["RequirementsSpec"]
    for key in ("inputs", "steps", "output_format", "business_rules", "edge_cases", "schedule"):
        assert key in spec_def["properties"]
