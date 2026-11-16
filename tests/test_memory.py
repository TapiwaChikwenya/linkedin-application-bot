"""Standing memory: approved answers enter the next prompt without a weight update."""

import json

from linkedin_easy_apply.llm import answer_unanswered, build_prompt
from linkedin_easy_apply.memory import compile_standing_memory, memory_status


def _row(
    ident: int,
    question: str,
    value: str,
    *,
    seen: str,
    reuse: int = 0,
    proposed: str = "",
    **extra,
) -> dict:
    row = {
        "id": ident,
        "raw_question": question,
        "approved_value": value,
        "proposed_value": proposed,
        "approval_status": "approved",
        "last_seen_at": seen,
        "reuse_count": reuse,
        "field_kind": "text",
    }
    row.update(extra)
    return row


def test_standing_memory_keeps_a_new_approval_ahead_of_high_reuse_contact_rows():
    rows = [
        _row(index, f"Question {index}", str(index), seen=f"2026-01-{index:02d}T00:00:00+00:00", reuse=30 - index)
        for index in range(1, 21)
    ]
    memory = compile_standing_memory(rows, limit=16, recent=8)
    ids = [item["source_id"] for item in memory]
    assert ids[:8] == [str(index) for index in range(20, 12, -1)]
    assert "1" in ids
    assert "9" not in ids
    assert len(memory) == 16


def test_standing_memory_drops_pending_files_and_duplicate_clusters():
    rows = [
        _row(1, "Years of GCP?", "5", seen="2026-02-01T00:00:00+00:00", proposed="9", cluster_key="gcp"),
        _row(2, "GCP years duplicate", "4", seen="2026-02-02T00:00:00+00:00", reuse=3, cluster_key="gcp"),
        _row(3, "Upload resume", "resume.pdf", seen="2026-02-03T00:00:00+00:00", field_kind="file"),
        _row(4, "Still pending", "Yes", seen="2026-02-04T00:00:00+00:00", approval_status="pending"),
        _row(5, "Hidden duplicate", "Yes", seen="2026-02-05T00:00:00+00:00", merged_into_id=1),
        _row(6, "Blank", "", seen="2026-02-06T00:00:00+00:00"),
    ]
    memory = compile_standing_memory(rows)
    assert len(memory) == 1
    assert memory[0]["source_id"] == "2"
    assert memory[0]["value"] == "4"
    assert "corrected" not in memory[0]


def test_corrected_approval_is_flagged():
    memory = compile_standing_memory([
        _row(7, "Years of SQL?", "5", seen="2026-03-01T00:00:00+00:00", proposed="9"),
    ])
    assert memory[0]["corrected"] == "true"
    assert memory[0]["value"] == "5"


def test_prompt_includes_standing_memory_for_a_different_question():
    prompt = build_prompt(
        {"first_name": "Ada"},
        "form",
        [{"question": "How many years of Databricks experience?", "kind": "number"}],
        standing_memory=[{
            "source_id": "11",
            "question": "How many years of Spark experience?",
            "value": "4",
            "reuse_count": "2",
            "corrected": "true",
        }],
    )
    assert '"standing_memory"' in prompt
    assert "How many years of Spark experience?" in prompt
    assert '"corrected": "true"' in prompt
    assert "Pending questions are not in standing_memory" in prompt


def test_answer_unanswered_sends_unrelated_approved_memory(monkeypatch, tmp_path):
    from linkedin_easy_apply import llm
    from linkedin_easy_apply.store import Store

    store = Store(str(tmp_path / "memory.sqlite"))
    spark = store.upsert_question(
        raw_question="What name should recruiters use?",
        field_kind="text",
        source="manual",
        proposed_value="Applicant",
    )
    store.approve_answer(spark["id"], "Sam")
    pending = store.upsert_question(
        raw_question="How many years of Airflow experience do you have?",
        field_kind="number",
        source="unanswered",
        proposed_value="3",
    )
    captured = {}

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else request
        if str(url).endswith("/api/tags"):
            return FakeResponse({"models": [{"name": "unit-model:latest"}]})
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({
            "response": json.dumps({
                "answers": [{
                    "question_id": "q0",
                    "value": "4",
                    "confidence": 0.8,
                    "source_ids": [],
                }]
            })
        })

    class Cfg:
        llm_mode = "ollama"
        ollama_host = "http://unit-ollama:11434"
        ollama_model = "unit-model"

    llm._STATUS_CACHE.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.urllib.request.urlopen", fake_urlopen)
    answers = answer_unanswered(
        {"first_name": "Ada"},
        "form",
        [{"question": "How many years of Databricks experience do you have?", "kind": "number"}],
        config_module=Cfg,
        store=store,
    )
    prompt = captured["body"]["prompt"]
    assert "What name should recruiters use?" in prompt
    assert '"standing_memory"' in prompt
    assert '"value": "Sam"' in prompt
    assert "How many years of Airflow experience do you have?" not in prompt
    assert answers[0]["value"] == "4"
    status = memory_status(store)
    assert status["approved"] >= 1
    assert status["pending"] >= 1
    assert status["in_prompt"] >= 1
    assert status["weights_frozen"] is True
    assert pending["approval_status"] == "pending"
    store.close()


def test_memory_status_empty_store_does_not_raise():
    assert memory_status(None)["in_prompt"] == 0
    assert memory_status(None)["weights_frozen"] is True
