"""Build pending seed questions so operators can grow approved RAG memory.

This does not train, fine-tune, or apply LoRA to Ollama. It only upserts
`pending` SQLite rows. `approved_value` stays empty until the operator approves
an answer in the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from linkedin_easy_apply.facts import (
    extract_resume_text,
    mapped_years_experience,
)
from linkedin_easy_apply.question_capture import normalize_question, option_set_hash

YES_NO = ("Yes", "No")
MAX_RESUME_TOOLS = 12
MAX_SEED_INSERTS = 80

_SKILL_LABELS = {
    "azure data factory": "Azure Data Factory",
    "data factory": "Data Factory",
    "sql server": "SQL Server",
    "power bi": "Power BI",
    "t-sql": "T-SQL",
    "tsql": "T-SQL",
    "databricks": "Databricks",
    "pyspark": "PySpark",
    "python": "Python",
    "ssis": "SSIS",
    "ssrs": "SSRS",
    "spark": "Spark",
    "azure": "Azure",
    "sql": "SQL",
    "terraform": "Terraform",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "oracle": "Oracle",
    "mongodb": "MongoDB",
}

# Resume scan needles. Longer phrases must stay above shorter ones.
RESUME_TOOL_HINTS = (
    ("azure data factory", "Azure Data Factory"),
    ("microsoft fabric", "Microsoft Fabric"),
    ("azure synapse", "Azure Synapse"),
    ("sql server", "SQL Server"),
    ("power bi", "Power BI"),
    ("apache airflow", "Apache Airflow"),
    ("apache spark", "Spark"),
    ("postgresql", "PostgreSQL"),
    ("databricks", "Databricks"),
    ("terraform", "Terraform"),
    ("kubernetes", "Kubernetes"),
    ("snowflake", "Snowflake"),
    ("synapse", "Azure Synapse"),
    ("airflow", "Apache Airflow"),
    ("pyspark", "PySpark"),
    ("python", "Python"),
    ("postgres", "PostgreSQL"),
    ("mongodb", "MongoDB"),
    ("spark", "Spark"),
    ("docker", "Docker"),
    ("kafka", "Kafka"),
    ("oracle", "Oracle"),
    ("ssis", "SSIS"),
    ("ssrs", "SSRS"),
    ("dbt", "dbt"),
    ("sql", "SQL"),
    ("azure", "Azure"),
)


@dataclass(frozen=True)
class SeedQuestion:
    raw_question: str
    field_kind: str
    options: tuple[str, ...] = field(default_factory=tuple)
    proposed_value: str = ""
    required: bool = False


def skill_label(key: str) -> str:
    text = " ".join(str(key or "").split())
    if not text:
        return ""
    return _SKILL_LABELS.get(text.casefold(), text.title())


def _yes_no(question: str, proposed: str = "", required: bool = True) -> SeedQuestion:
    return SeedQuestion(
        raw_question=question,
        field_kind="select",
        options=YES_NO,
        proposed_value=proposed,
        required=required,
    )


def common_easy_apply_patterns() -> list[SeedQuestion]:
    """Frequent LinkedIn Easy Apply prompts. Proposed values are review hints only."""
    return [
        _yes_no(
            "Will you now or in the future require sponsorship for an employment visa?",
            "No",
        ),
        _yes_no("Are you legally authorized to work in the United States?", "Yes"),
        _yes_no("Are you willing to relocate?", "Yes"),
        _yes_no("Are you willing to work remotely?"),
        _yes_no("Are you willing to work hybrid?"),
        _yes_no("Are you willing to work on-site?"),
        _yes_no("Do you have a valid driver's license?"),
        _yes_no("Are you at least 18 years of age?"),
        _yes_no("Are you willing to complete a background check?"),
        _yes_no("Are you willing to take a drug test?"),
        _yes_no("Have you previously been employed by this company?", "No"),
        _yes_no("Have you previously interviewed with this company?", "No"),
        _yes_no("Are you currently employed by this company?", "No"),
        _yes_no("Do you have an active security clearance?", "No"),
        SeedQuestion(
            raw_question="How many years of professional experience do you have?",
            field_kind="number",
            required=True,
        ),
    ]


def years_prompts_from_config(years_experience: dict[str, Any] | None) -> list[SeedQuestion]:
    seeds: list[SeedQuestion] = []
    for skill, years in mapped_years_experience(years_experience or {}).items():
        label = skill_label(skill)
        if not label:
            continue
        seeds.append(
            SeedQuestion(
                raw_question=f"How many years of {label} experience do you have?",
                field_kind="number",
                proposed_value=str(years),
                required=True,
            )
        )
    return seeds


def tools_from_resume(resume_text: str, known_labels: set[str]) -> list[str]:
    blob = " ".join(str(resume_text or "").casefold().split())
    if not blob:
        return []
    found: list[str] = []
    seen: set[str] = {item.casefold() for item in known_labels}
    for needle, label in RESUME_TOOL_HINTS:
        key = label.casefold()
        if key in seen:
            continue
        if needle not in blob:
            continue
        seen.add(key)
        found.append(label)
        if len(found) >= MAX_RESUME_TOOLS:
            break
    return found


def resume_prompts(resume_text: str, known_labels: set[str]) -> list[SeedQuestion]:
    seeds: list[SeedQuestion] = []
    for label in tools_from_resume(resume_text, known_labels):
        seeds.append(
            SeedQuestion(
                raw_question=f"How many years of {label} experience do you have?",
                field_kind="number",
                required=True,
            )
        )
        seeds.append(
            _yes_no(f"Do you have experience with {label}?")
        )
    return seeds


def _identity(seed: SeedQuestion) -> tuple[str, str, str]:
    kind = str(seed.field_kind or "text").lower() or "text"
    options = [str(option) for option in seed.options if str(option).strip()]
    return (normalize_question(seed.raw_question), kind, option_set_hash(kind, options))


def build_seed_questions(
    config_module: Any | None = None,
    resume_text: str | None = None,
) -> list[SeedQuestion]:
    """Deduped catalog of pending seed prompts. Never includes approved answers."""
    import config as default_config

    cfg = config_module or default_config
    years = dict(getattr(cfg, "years_experience", {}) or {})
    if resume_text is None:
        from linkedin_easy_apply.operator_settings import resolved_resume_path

        resume_path = resolved_resume_path(config_module=cfg) or str(
            getattr(cfg, "resume_path", "") or ""
        )
        resume_text = extract_resume_text(resume_path)
    known_labels = {skill_label(skill) for skill in mapped_years_experience(years)}
    catalog = [
        *common_easy_apply_patterns(),
        *years_prompts_from_config(years),
        *resume_prompts(str(resume_text or ""), known_labels),
    ]
    unique: list[SeedQuestion] = []
    seen: set[tuple[str, str, str]] = set()
    for seed in catalog:
        identity = _identity(seed)
        if not identity[0] or identity in seen:
            continue
        seen.add(identity)
        unique.append(seed)
    return unique


def generate_seed_questions(
    store: Any,
    config_module: Any | None = None,
    resume_text: str | None = None,
    max_inserts: int = MAX_SEED_INSERTS,
) -> dict[str, int | str | bool]:
    """Insert new pending seeds. Existing identity rows are left untouched."""
    seeds = build_seed_questions(config_module=config_module, resume_text=resume_text)
    source_name = getattr(store, "seed_source_name", lambda: "manual")()
    source = str(source_name or "manual")
    if source not in {"seed", "manual"}:
        source = "manual"
    inserted = 0
    skipped = 0
    cap = max(0, int(max_inserts))
    capped = False
    for seed in seeds:
        if inserted >= cap:
            capped = True
            break
        exists = getattr(store, "question_identity_exists", None)
        if callable(exists) and exists(seed.raw_question, seed.field_kind, list(seed.options)):
            skipped += 1
            continue
        row = store.upsert_question(
            raw_question=seed.raw_question,
            field_kind=seed.field_kind,
            options=list(seed.options),
            required=seed.required,
            source=source,
            proposed_value=seed.proposed_value,
            provenance="seed",
            increment=False,
        )
        if row is None:
            skipped += 1
            continue
        if str(row.get("approved_value") or "").strip():
            skipped += 1
            continue
        inserted += 1
    return {
        "inserted": inserted,
        "skipped": skipped,
        "candidates": len(seeds),
        "source": source,
        "capped": capped,
    }
