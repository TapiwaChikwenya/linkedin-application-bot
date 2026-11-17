"""Per-role month/year assignment. Dates are never invented."""

import pytest

from linkedin_easy_apply.operator_settings import parse_work_history
from linkedin_easy_apply.page_snapshot import (
    grounded_control_value,
    stamp_work_history,
)

MONTHS = [
    "Month",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
YEARS = ["Year", "2019", "2020", "2021", "2022", "2023", "2024"]


def _month(question: str = "Month") -> dict:
    return {
        "question": question,
        "kind": "select",
        "required": True,
        "value": "Month",
        "options": list(MONTHS),
    }


def _year(question: str = "Year") -> dict:
    return {
        "question": question,
        "kind": "select",
        "required": True,
        "value": "Year",
        "options": list(YEARS),
    }


def test_two_roles_fill_start_and_end_from_the_list():
    fields = [
        {"question": "Company", "kind": "text", "required": True, "value": ""},
        _month(),
        _year(),
        _month(),
        _year(),
        {"question": "Company", "kind": "text", "required": True, "value": ""},
        _month(),
        _year(),
    ]
    entries = parse_work_history(
        [
            {
                "company": "First",
                "title": "Engineer",
                "start_month": "Mar",
                "start_year": "2019",
                "end_month": "June",
                "end_year": "2021",
                "current": False,
            },
            {
                "company": "Second",
                "title": "Lead",
                "start_month": "July",
                "start_year": "2021",
                "current": True,
            },
        ]
    )
    assert stamp_work_history(fields, entries) == ""
    assert grounded_control_value(fields[1]) == "March"
    assert grounded_control_value(fields[2]) == "2019"
    assert grounded_control_value(fields[3]) == "June"
    assert grounded_control_value(fields[4]) == "2021"
    assert grounded_control_value(fields[6]) == "July"
    assert grounded_control_value(fields[7]) == "2021"


def test_missing_role_skips_instead_of_reusing_one_month():
    fields = [_month(), _year(), {"question": "Company", "kind": "text", "value": ""}, _month(), _year()]
    reason = stamp_work_history(
        fields,
        [{"company": "Only", "title": "Engineer", "start_month": "March", "start_year": "2019", "current": True}],
    )
    assert reason == "Unanswered required question: work history"
    assert grounded_control_value(fields[3]) == ""


def test_current_role_does_not_require_an_end_date_when_the_checkbox_exists():
    fields = [
        _month(),
        _year(),
        _month(),
        _year(),
        {"question": "I currently work here", "kind": "checkbox", "required": False, "checked": False},
    ]
    reason = stamp_work_history(
        fields,
        parse_work_history(
            [
                {
                    "company": "Now",
                    "title": "Engineer",
                    "start_month": "March",
                    "start_year": "2019",
                    "current": True,
                }
            ]
        ),
    )
    assert reason == ""
    assert fields[4]["_history_value"] == "Yes"
    assert fields[2].get("_history_skip") is True
    assert grounded_control_value(fields[2]) == ""


def test_single_start_pair_still_uses_config_month(monkeypatch):
    import config

    fields = [_month(), _year()]
    assert stamp_work_history(fields, []) == ""
    monkeypatch.setattr(config, "work_history_month", "March", raising=False)
    monkeypatch.setattr(config, "work_history_year", "2019", raising=False)
    assert grounded_control_value(fields[0]) == "March"
    assert grounded_control_value(fields[1]) == "2019"


def test_parse_work_history_rejects_a_bad_month():
    with pytest.raises(ValueError):
        parse_work_history([{"company": "X", "start_month": "Smarch", "start_year": "2019"}])
