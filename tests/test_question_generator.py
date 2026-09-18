from types import SimpleNamespace

from linkedin_easy_apply.facts import mapped_years_experience, mapped_yes_no_rules
from linkedin_easy_apply.question_generator import (
    build_seed_questions,
    generate_seed_questions,
    skill_label,
)
from linkedin_easy_apply.store import Store

_Cfg = SimpleNamespace(
    years_experience={"sql": 9, "python": 5, "default": 9},
    yes_no_answers=[(("sponsorship",), "No")],
    resume_path="",
)


def test_skill_label_keeps_common_acronyms():
    assert skill_label("sql") == "SQL"
    assert skill_label("azure data factory") == "Azure Data Factory"


def test_build_seed_questions_covers_patterns_and_config_years():
    seeds = build_seed_questions(_Cfg, resume_text="Built Apache Airflow DAGs and dbt models.")
    texts = [seed.raw_question for seed in seeds]
    kinds = {(seed.raw_question, seed.field_kind) for seed in seeds}
    assert "Will you now or in the future require sponsorship for an employment visa?" in texts
    assert "Are you legally authorized to work in the United States?" in texts
    assert "Are you willing to relocate?" in texts
    assert ("How many years of SQL experience do you have?", "number") in kinds
    assert ("How many years of Python experience do you have?", "number") in kinds
    assert "How many years of Apache Airflow experience do you have?" in texts
    assert "Do you have experience with dbt?" in texts
    assert all(seed.field_kind != "file" for seed in seeds)
    sql = next(seed for seed in seeds if seed.raw_question.startswith("How many years of SQL"))
    assert sql.proposed_value == "9"
    generic = next(
        seed for seed in seeds if seed.raw_question == "How many years of professional experience do you have?"
    )
    assert generic.proposed_value == ""


def test_generate_seed_questions_is_idempotent_and_pending_only(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    first = generate_seed_questions(store, _Cfg, resume_text="Airflow and Snowflake")
    assert first["inserted"] > 0
    assert first["inserted"] == first["candidates"]
    assert first["skipped"] == 0
    rows = store.list_questions(limit=500)
    assert rows
    assert all(row["approval_status"] == "pending" for row in rows)
    assert all(not str(row.get("approved_value") or "").strip() for row in rows)
    assert all(row["source"] in {"seed", "manual"} for row in rows)
    assert all(row["provenance"] == "seed" for row in rows)
    pending_ids = {row["id"] for row in rows}
    second = generate_seed_questions(store, _Cfg, resume_text="Airflow and Snowflake")
    assert second["inserted"] == 0
    assert second["skipped"] == first["inserted"]
    again = store.list_questions(limit=500)
    assert {row["id"] for row in again} == pending_ids
    assert store.question_counts()["pending"] == len(pending_ids)
    store.close()


def test_generator_does_not_duplicate_linkedin_captured_identity(tmp_path):
    store = Store(str(tmp_path / "assistant.sqlite"))
    existing = store.upsert_question(
        raw_question="Are you legally authorized to work in the United States?",
        field_kind="select",
        options=["Yes", "No"],
        source="unanswered",
    )
    result = generate_seed_questions(store, _Cfg, resume_text="")
    assert result["skipped"] >= 1
    matches = [
        row
        for row in store.list_questions(limit=500)
        if row["normalized_question"] == existing["normalized_question"]
        and row["field_kind"] == "select"
        and row["option_set_hash"] == existing["option_set_hash"]
    ]
    assert len(matches) == 1
    assert matches[0]["id"] == existing["id"]
    store.close()


def test_catchall_maps_are_stripped_from_bootstrap_helpers():
    years = mapped_years_experience({"sql": 9, "default": 12})
    assert "sql" in years
    assert "default" not in years
    rules = mapped_yes_no_rules([
        (("sponsorship",), "No"),
        (("experience",), "Yes"),
        (("years", "experience"), "Yes"),
    ])
    assert rules == [(("sponsorship",), "No")]
