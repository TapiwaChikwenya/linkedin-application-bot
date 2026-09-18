import json

from linkedin_easy_apply.question_capture import append_questions


def test_question_capture_appends_metadata_without_answer_values(tmp_path):
    path = tmp_path / "questions.jsonl"
    count = append_questions(
        [
            {
                "question": "Preferred contact for person@example.com",
                "kind": "select",
                "options": ["person@example.com", "+1 555 123 4567"],
                "required": True,
                "_mapped_value_present": True,
                "_llm_considered": False,
                "_answered": True,
                "value": "secret answer",
            }
        ],
        job_id="123",
        title="Data Engineer",
        company="Acme",
        path=str(path),
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    assert count == 1
    assert record["question"] == "Preferred contact for [REDACTED_EMAIL]"
    assert record["options"] == ["[REDACTED_EMAIL]", "[REDACTED_PHONE]"]
    assert record["mapped_value_present"] is True
    assert record["filled"] is True
    assert "secret answer" not in path.read_text(encoding="utf-8")


def test_question_capture_is_append_only(tmp_path):
    path = tmp_path / "questions.jsonl"
    field = {"question": "Years of Python?", "kind": "number"}
    append_questions([field], job_id="1", path=str(path))
    append_questions([field], job_id="2", path=str(path))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
