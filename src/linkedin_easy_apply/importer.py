"""Import legacy daily text logs into SQLite."""

from __future__ import annotations

import os
from glob import glob

from linkedin_easy_apply.store import Store, extract_job_id, status_from_result


def parse_log_line(line: str) -> dict[str, str] | None:
    text = (line or "").strip()
    if not text or text.startswith(("----", "Category:")):
        return None
    parts = [part.strip() for part in text.split(" | ")]
    if len(parts) < 2:
        return None
    result = parts[-1]
    job_id = extract_job_id(result)
    if not job_id:
        return None
    title = parts[1] if len(parts) > 1 else ""
    company = parts[2] if len(parts) > 2 else ""
    location = parts[3] if len(parts) > 3 else ""
    workplace = parts[4] if len(parts) > 4 else ""
    url = ""
    if "http" in result:
        url = result[result.index("http") :].strip()
    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "workplace": workplace,
        "url": url,
        "status": status_from_result(result),
        "reason": result,
    }


def import_text_logs(store: Store, data_dir: str = "data") -> int:
    pattern = os.path.join(data_dir, "Applied Jobs DATA - *.txt")
    imported = 0
    for path in sorted(glob(pattern)):
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for line in lines:
            parsed = parse_log_line(line)
            if not parsed:
                continue
            store.upsert_job(
                parsed["job_id"],
                title=parsed["title"],
                company=parsed["company"],
                location=parsed["location"],
                workplace=parsed["workplace"],
                url=parsed["url"],
                status=parsed["status"],
                reason=parsed["reason"],
            )
            imported += 1
    return imported
