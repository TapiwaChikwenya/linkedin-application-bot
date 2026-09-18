"""Record structured job outcomes from the Selenium worker."""

from __future__ import annotations

from linkedin_easy_apply.job_metrics import classify_job, metrics_payload_json
from linkedin_easy_apply.store import Store, extract_job_id


def record_outcome(
    store: Store | None,
    run_id: str | None,
    *,
    url: str,
    title: str = "",
    company: str = "",
    location: str = "",
    location_text: str = "",
    workplace: str = "",
    status: str,
    reason: str = "",
    screenshot_path: str = "",
    html_path: str = "",
    description: str = "",
    sector: str = "",
    market: str = "",
    already_open: bool = False,
    metrics_json: str = "",
    fit_score: float | None = None,
) -> None:
    if store is None:
        return
    job_id = extract_job_id(url)
    resolved_location = location_text or location
    if not job_id:
        if run_id:
            store.add_event(run_id, "info", None, reason or status)
        return
    classified = classify_job(
        title,
        company,
        snippet=description,
        location=resolved_location,
        already_open=already_open,
        sector=sector,
        market=market,
        status=status,
    )
    payload = classified.as_payload(
        job_id=job_id,
        title=title,
        company=company,
        outcome=classified.outcome,
    )
    encoded = metrics_json or metrics_payload_json(payload)
    store.upsert_job(
        job_id,
        title=title,
        company=company,
        location=location or resolved_location,
        location_text=classified.location_text or resolved_location,
        sector=classified.sector,
        market=classified.market,
        outcome=classified.outcome,
        workplace=workplace,
        url=url,
        status=status,
        reason=reason,
        run_id=run_id,
        screenshot_path=screenshot_path,
        html_path=html_path,
        metrics_json=encoded,
        fit_score=fit_score,
    )
    if run_id:
        store.add_event(run_id, "info", job_id, reason or status, payload_json=encoded)
