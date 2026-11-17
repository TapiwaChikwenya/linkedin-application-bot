"""Redacted job pages from the four failure shapes. No personal answers."""

from pathlib import Path

from linkedin_easy_apply.job_page import parse_job_page

FIXTURES = Path(__file__).parent / "fixtures" / "job_pages"


def _read(name: str) -> dict[str, str]:
    return parse_job_page((FIXTURES / name).read_text(encoding="utf-8"))


def test_redacted_pages_keep_location_and_stop_at_the_job_block():
    expected = {
        "vatica.html": ("United States", "Remote", "6 days ago"),
        "scale_media.html": ("Burbank, CA", "Remote", "Reposted 6 days ago"),
        "simbe.html": ("United States", "Remote", "3 days ago"),
        "crossing_hurdles.html": ("United States", "Remote", "Reposted 9 hours ago"),
    }
    for name, (location, workplace, posted) in expected.items():
        parsed = _read(name)
        assert parsed["location"] == location
        assert parsed["workplace"] == workplace
        assert parsed["posted"] == posted
        assert len(parsed["description"]) > 80
        lowered = parsed["description"].casefold()
        assert "about the company" not in lowered
        assert "people also viewed" not in lowered
        assert "set alert" not in lowered
