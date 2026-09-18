from linkedin_easy_apply.metrics import (
    build_metrics,
    classify_location,
    classify_market,
    classify_sector,
    outcome_bucket,
)


def test_classifies_sector_and_market():
    assert classify_sector("Senior Data Engineer", "Northwind") == "data_eng"
    assert classify_sector("SQL Developer", "") == "sql"
    assert classify_market("Data Engineer", "JPMorgan Chase") == "finance"
    assert classify_market("Recruiter", "Robert Half") == "staffing"
    assert classify_market("Nurse", "UnitedHealth") == "healthcare"
    assert classify_location("") == "unknown"
    assert classify_location("  United States ") == "United States"
    assert outcome_bucket("skipped_filter") == "skipped"
    assert outcome_bucket("applied") == "applied"


def test_build_metrics_today_and_histograms():
    jobs = [
        {
            "title": "Azure Data Engineer",
            "company": "Microsoft",
            "location": "United States",
            "status": "applied",
            "seen_at": "2026-09-18T01:00:00",
            "applied_at": "2026-09-18T01:00:00",
        },
        {
            "title": "Staffing recruiter",
            "company": "Insight Global",
            "location": "Remote",
            "status": "skipped_filter",
            "seen_at": "2026-09-18T02:00:00",
        },
        {
            "title": "Nurse",
            "company": "Hospital Corp",
            "location": "United States",
            "status": "failed",
            "seen_at": "2026-09-17T02:00:00",
        },
    ]
    payload = build_metrics(jobs, today="2026-09-18")
    assert payload["source"] == "derived"
    assert payload["applied_vs_skipped"] == {"applied": 1, "skipped": 1, "failed": 0}
    assert payload["today"]["totals"]["applied"] == 1
    assert payload["today"]["by_outcome"]
    assert payload["last_7_days"]["totals"]["failed"] == 1
    assert payload["last_7_days"]["by_sector"]
    sectors = {item["label"]: item for item in payload["sectors"]}
    assert sectors["data_eng"]["applied"] == 1
    markets = {item["label"]: item for item in payload["markets"]}
    assert "tech" in markets or "staffing" in markets
    locations = {item["label"]: item for item in payload["locations"]}
    assert locations["United States"]["total"] == 2
