"""Guards for stuck Easy Apply steps, failed generates, and job-page text."""

from selenium.common.exceptions import StaleElementReferenceException

import config
from linkedin import Linkedin
from linkedin_easy_apply.job_card import SearchCard, job_id_from_markup
from linkedin_easy_apply.job_fit import evaluate_listing_fit
from linkedin_easy_apply.job_page import parse_job_page
from linkedin_easy_apply.page_snapshot import (
    control_shape,
    is_inline_validation,
    page_counter_from_text,
    required_field_fingerprint,
    unanswered_required_reason,
)
from linkedin_easy_apply.search_page import COLLECT_CARDS_JS

JOB_PAGE_HTML = """
<html>
  <head><title>Senior Data Engineer | Example Co | LinkedIn</title></head>
  <body>
    <h1>Senior Data Engineer</h1>
    <a href="https://www.linkedin.com/company/example-co/">Example Co</a>
    <p>
      <span>United States</span>
      <span> · </span>
      <span>6 days ago</span>
      <span> · </span>
      <span>Over 100 applicants</span>
    </p>
    <span>Remote</span>
    <span>Full-time</span>
    <div id="JobDetails_AboutTheJob_1">
      <h2>About the job</h2>
      <span data-testid="expandable-text-box">
        <p>We build data platforms for hospitals and payers across the United States.
        This description is long enough to score job fit against a real listing
        and should be extracted even when hashed CSS classes change.</p>
      </span>
    </div>
  </body>
</html>
"""

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
YEARS = ["Year"] + [str(year) for year in range(2024, 2010, -1)]


class _Message:
    def __init__(self, text: str):
        self.text = text

    def is_displayed(self) -> bool:
        return True


class _Dialog:
    def __init__(self, text: str, messages=None):
        self.text = text
        self.messages = list(messages or [])

    def find_elements(self, _by, selector):
        lowered = str(selector).lower()
        if any(token in lowered for token in ("alert", "helper", "error", "feedback")):
            return list(self.messages)
        return []


class _Driver:
    def execute_script(self, script, node=None, *_args):
        if node is not None and "scrollTop" not in str(script):
            return getattr(node, "text", "") or ""
        return ""

    def find_elements(self, *_args, **_kwargs):
        return []


class _Wait:
    def __init__(self, finder):
        self._finder = finder

    def until(self, condition):
        return condition(None)


def _bot(finder, clicks, fills):
    bot = Linkedin.__new__(Linkedin)
    bot.driver = _Driver()
    bot.wait = _Wait(finder)
    bot.findApplicationContainer = finder
    bot.observe = lambda *_args, **_kwargs: None
    bot.fillKnownFields = lambda dialog, job_id=None: fills(dialog)
    bot.findApplicationAction = lambda dialog, wanted: (
        None if "submit" in wanted else "next"
    )
    bot.applicationAction = lambda _action: "next"
    bot.clickApplicationAction = lambda _action: clicks.append("next")
    bot.dismissApplication = lambda: None
    bot.applicationFailure = lambda dialog, offer, job, reason="": "* 🥵 " + reason
    bot.commit_answer_memory = lambda: None
    return bot


def test_unchanged_fingerprint_stops_when_the_page_counter_is_missing(monkeypatch):
    import linkedin as linkedin_module

    monkeypatch.setattr(linkedin_module, "sleep_human", lambda *_args, **_kwargs: 0)
    dialog = _Dialog("Easy Apply")
    fields = [{
        "question": "LinkedIn",
        "kind": "text",
        "required": True,
        "value": "",
        "checked": False,
    }]
    clicks = []
    bot = _bot(lambda: dialog, clicks, lambda _dialog: "")
    bot.collect_form_state = lambda _dialog: (fields, "")
    result, applied = bot.applyProcess("https://www.linkedin.com/jobs/view/1", "1")
    assert applied is False
    assert clicks == ["next"]
    assert "did not change after Next" in result
    assert required_field_fingerprint(fields)


def test_location_typeahead_failure_is_a_skip_and_does_not_click_next(monkeypatch):
    from selenium.common.exceptions import WebDriverException

    import linkedin as linkedin_module

    monkeypatch.setattr(linkedin_module, "sleep_human", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(config, "application_city", "Dallas, Texas", raising=False)

    class Broken:
        def get_attribute(self, _name):
            return ""

        def click(self):
            raise WebDriverException("typeahead")

        def send_keys(self, *_args):
            return None

    bot = Linkedin.__new__(Linkedin)
    bot.pace = lambda: "fast"
    bot.observe = lambda *_args, **_kwargs: None
    bot.findFieldByLabel = lambda *_args, **_kwargs: None
    field = {
        "question": "Location (city)",
        "kind": "text",
        "required": True,
        "value": "",
        "_element": Broken(),
    }
    reason = bot.fill_location_typeahead(None, [field], "4482544960")
    assert reason == "Unanswered required question: Location (city)"

    clicks = []
    dialog = _Dialog("1/2 pages")
    guarded = _bot(lambda: dialog, clicks, lambda _dialog: reason)
    result, applied = guarded.applyProcess("https://www.linkedin.com/jobs/view/1", "1")
    assert applied is False
    assert clicks == []
    assert "Location (city)" in result


def test_validation_phrase_and_page_counter_ignore_percent():
    assert is_inline_validation("This field is required")
    assert not is_inline_validation("6 of 150 characters")
    assert page_counter_from_text("80 percent complete 4/5 pages") == 4
    assert page_counter_from_text("80 percent complete") is None


def test_validation_text_without_role_alert_stops_the_step(monkeypatch):
    import linkedin as linkedin_module

    monkeypatch.setattr(linkedin_module, "sleep_human", lambda *_args, **_kwargs: 0)
    state = {"clicked": False}
    dialog = _Dialog("4/5 pages")

    def finder():
        if state["clicked"]:
            dialog.messages = [_Message("This field is required")]
        return dialog

    clicks = []

    def fill(_dialog):
        return ""

    def click(_action):
        state["clicked"] = True
        clicks.append("next")

    bot = _bot(finder, clicks, fill)
    bot.clickApplicationAction = click
    result, applied = bot.applyProcess("https://www.linkedin.com/jobs/view/1", "1")
    assert applied is False
    assert clicks == ["next"]
    assert "This field is required" in result
    assert "10 steps" not in result


def test_page_counter_not_moving_stops_the_loop(monkeypatch):
    import linkedin as linkedin_module

    monkeypatch.setattr(linkedin_module, "sleep_human", lambda *_args, **_kwargs: 0)
    dialog = _Dialog("4/5 pages")
    clicks = []
    bot = _bot(lambda: dialog, clicks, lambda _dialog: "")
    result, applied = bot.applyProcess("https://www.linkedin.com/jobs/view/1", "1")
    assert applied is False
    assert clicks == ["next"]
    assert "did not advance past 4" in result


def test_advancing_page_counter_does_not_stop(monkeypatch):
    import linkedin as linkedin_module

    monkeypatch.setattr(linkedin_module, "sleep_human", lambda *_args, **_kwargs: 0)
    state = {"clicked": False}
    first = _Dialog("4/5 pages")
    second = _Dialog("5/5 pages")
    clicks = []

    def finder():
        return second if state["clicked"] else first

    def fill(_dialog):
        return "filled the next page" if state["clicked"] else ""

    def click(_action):
        state["clicked"] = True
        clicks.append("next")

    bot = _bot(finder, clicks, fill)
    bot.clickApplicationAction = click
    result, applied = bot.applyProcess("https://www.linkedin.com/jobs/view/1", "1")
    assert applied is False
    assert clicks == ["next"]
    assert result.startswith("* Skipped: filled the next page")


def test_stale_dialog_is_retried_and_missing_modal_is_explicit(monkeypatch):
    import linkedin as linkedin_module

    monkeypatch.setattr(linkedin_module, "sleep_human", lambda *_args, **_kwargs: 0)
    state = {"clicked": False, "stale_hits": 0}
    first = _Dialog("3/5 pages")
    recovered = _Dialog("4/5 pages")
    clicks = []

    def finder():
        if not state["clicked"]:
            return first
        state["stale_hits"] += 1
        if state["stale_hits"] <= 2:
            raise StaleElementReferenceException("stale")
        return recovered

    def fill(_dialog):
        return "continued after stale lookup" if state["clicked"] else ""

    def click(_action):
        state["clicked"] = True
        clicks.append("next")

    bot = _bot(finder, clicks, fill)
    bot.clickApplicationAction = click
    result, applied = bot.applyProcess("https://www.linkedin.com/jobs/view/1", "1")
    assert applied is False
    assert clicks == ["next"]
    assert state["stale_hits"] >= 2
    assert "Easy Apply dialog went stale" not in result
    assert result.startswith("* Skipped: continued after stale lookup")

    missing_clicks = []
    missing = _bot(
        lambda: first if not missing_clicks else None,
        missing_clicks,
        lambda _dialog: "",
    )
    missing.clickApplicationAction = lambda _action: missing_clicks.append("next")
    closed, applied = missing.applyProcess("https://www.linkedin.com/jobs/view/1", "1")
    assert applied is False
    assert missing_clicks == ["next"]
    assert "modal closed before the application was submitted" in closed


def test_request_failure_is_not_declined_and_does_not_click_next(monkeypatch):
    import linkedin as linkedin_module
    from linkedin_easy_apply.llm import AnswerRequestError

    monkeypatch.setattr(linkedin_module, "sleep_human", lambda *_args, **_kwargs: 0)
    logs = []
    calls = {"generate": 0}

    def generate(self, *_args, **_kwargs):
        calls["generate"] += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr(
        "linkedin_easy_apply.llm.status",
        lambda config_module=None: {"ready": True, "host": "http://127.0.0.1:11434", "model": "unit"},
    )
    monkeypatch.setattr("linkedin_easy_apply.llm.OllamaClient.generate", generate)
    from linkedin_easy_apply.llm import answer_unanswered

    try:
        answer_unanswered(
            {"summary": "x"},
            "form",
            [{"question": "LinkedIn", "kind": "text", "required": True}],
        )
    except AnswerRequestError as exc:
        assert exc.kind == "timeout"
        assert exc.elapsed_sec >= 0
    else:
        raise AssertionError("timeout must not become an empty answer list")
    assert calls["generate"] == 1

    bot = Linkedin.__new__(Linkedin)
    bot.observe = lambda message, *_args, **_kwargs: logs.append(message)
    bot.store = None
    bot.driver = _Driver()

    def fail(*_args, **_kwargs):
        raise AnswerRequestError("unreachable", 1.5, "Ollama not reachable")

    monkeypatch.setattr("linkedin_easy_apply.llm.answer_unanswered", fail)
    bot.fill_llm_fields(
        [{
            "question": "Favorite color?",
            "kind": "text",
            "required": True,
            "value": "",
            "_element": object(),
        }],
        "form",
        "4490565483",
    )
    assert bot._llm_request_failed is True
    assert any(line.startswith("Ollama request failed (unreachable, 1.5s)") for line in logs)
    assert not any("declined" in line for line in logs)

    logs.clear()
    monkeypatch.setattr("linkedin_easy_apply.llm.answer_unanswered", lambda *_args, **_kwargs: [])
    bot._llm_request_failed = False
    bot.fill_llm_fields(
        [{
            "question": "Favorite color?",
            "kind": "text",
            "required": True,
            "value": "",
            "_element": object(),
        }],
        "form",
        "4490565483",
    )
    assert any("Ollama declined to answer (accepted=0)" in line for line in logs)
    assert bot._llm_request_failed is False

    clicks = []
    progress = _bot(lambda: _Dialog("2/5 pages"), clicks, lambda _dialog: (
        "Ollama request failed; leaving this application for review"
    ))
    result, applied = progress.applyProcess("https://www.linkedin.com/jobs/view/1", "1")
    assert applied is False
    assert clicks == []
    assert "Ollama request failed" in result


def test_job_page_extracts_description_and_primary_line():
    parsed = parse_job_page(JOB_PAGE_HTML)
    assert parsed["location"] == "United States"
    assert parsed["posted"] == "6 days ago"
    assert "100 applicants" in parsed["applications"]
    assert parsed["workplace"] == "Remote"
    assert "data platforms" in parsed["description"]
    assert "About the job" not in parsed["description"]

    bot = Linkedin.__new__(Linkedin)

    class PageDriver:
        page_source = JOB_PAGE_HTML
        title = ""

        def find_elements(self, *_args, **_kwargs):
            return []

    bot.driver = PageDriver()
    details = bot.getJobProperties(1)
    assert details["location"] == "United States"
    assert "United States" in details["line"]
    assert "data platforms" in details["description"]
    assert bot.extract_job_description().startswith("We build data platforms")


def test_audit_line_includes_location_after_backfill():
    details = {
        "title": "Senior Microsoft Fabric Data Engineer",
        "company": "Risamsoft Inc",
        "location": "",
        "workplace": "",
        "posted": "",
        "applications": "",
        "line": "1 | Senior Microsoft Fabric Data Engineer | Risamsoft Inc | | | | ",
    }
    details["location"] = "United States"
    details["line"] = Linkedin.format_job_audit_line(1, details)
    assert details["line"] == (
        "1 | Senior Microsoft Fabric Data Engineer | Risamsoft Inc | United States |  |  | "
    )


def test_fit_logs_selector_miss_when_the_open_page_has_no_description():
    bot = Linkedin.__new__(Linkedin)
    bot.extract_job_description = lambda: ""
    snippet, source = bot.resolve_fit_snippet(
        {"snippet": "tiny", "description": "", "page_open": True}
    )
    assert source == "selector_miss"
    result = evaluate_listing_fit(
        title="Data Engineer",
        company="Acme",
        snippet=snippet,
        source=source,
        generate_json=lambda *_args, **_kwargs: {"fit": 0.9, "reason": "no", "decision": "apply"},
    )
    assert result.needs_description is True
    assert result.log_line() == "Job fit llama deferred: description selector miss"


def test_skip_and_summary_are_logged_once(monkeypatch):
    import linkedin as linkedin_module

    events = []
    monkeypatch.setattr(linkedin_module, "prYellow", lambda *_args, **_kwargs: events.append("yellow"))
    bot = Linkedin.__new__(Linkedin)
    bot.displayWriteResults = lambda line: events.append(line)
    bot.observe = lambda *_args, **_kwargs: events.append("observe")
    bot.log_page_matches(0, 25, 17, 8)
    assert events == [
        "Search page 1: opening 17 matching job(s) out of 25 cards (8 filtered before open)."
    ]

    events.clear()
    card = SearchCard(
        job_id="",
        title="Senior Data Solution Engineer",
        company="Aretum",
        location="McLean, VA (Remote)",
    )
    bot.record_card_skip(card, ("skipped_filter", "Skipped by title/company filter"))
    assert len(events) == 1
    assert "Job: unknown" in events[0]
    assert "observe" not in events


def test_job_id_comes_from_data_occludable_job_id():
    html = '<div role="button" data-occludable-job-id="4490565483">Simbe</div>'
    assert job_id_from_markup(html) == "4490565483"
    assert "data-occludable-job-id" in COLLECT_CARDS_JS
    assert "listedJobId" in COLLECT_CARDS_JS

    bot = Linkedin.__new__(Linkedin)
    bot.stored_job = lambda _job_id: None
    bot._activate_semantic_card = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("filtered card must not be clicked to discover an id")
    )
    card, skip = bot.prepare_semantic_card(
        0,
        {
            "paras": ["Junior Recruiter", "Aretum", "McLean, VA (Remote)"],
            "job_id": job_id_from_markup(
                '<li data-occludable-job-id="4482544960"></li>'
            ),
        },
    )
    assert card.job_id == "4482544960"
    assert skip is not None

    class Offer:
        def get_attribute(self, name):
            if name == "data-occludable-job-id":
                return ""
            if name == "outerHTML":
                return '<li data-occludable-job-id="4482925672"></li>'
            return ""

    bot.read_search_card = lambda _offer, job_id="": SearchCard(
        job_id=job_id,
        title="Role",
        company="Crossing Hurdles",
        apply_kind="easy_apply",
    )
    found, _decision = bot.inspect_search_card(Offer())
    assert found.job_id == "4482925672"


def test_date_and_month_year_use_grounded_values_or_abort(monkeypatch):
    import config
    import linkedin as linkedin_module

    monkeypatch.setattr(linkedin_module, "sleep_human", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(config, "LinkedInProfileURL", "https://www.linkedin.com/in/example", raising=False)
    monkeypatch.setattr(config, "earliest_start_date", "04/01/2026", raising=False)
    monkeypatch.setattr(config, "work_history_month", "March", raising=False)
    monkeypatch.setattr(config, "work_history_year", "2019", raising=False)

    class SelectElement:
        def __init__(self):
            self.selected = ""

        def get_attribute(self, _name):
            return ""

    class TextElement:
        def __init__(self):
            self.keys = []

        def get_attribute(self, _name):
            return ""

        def click(self):
            return None

        def send_keys(self, *args):
            self.keys.append(args)

    class FakeSelect:
        def __init__(self, element):
            self.element = element

        def select_by_visible_text(self, text):
            self.element.selected = text

    monkeypatch.setattr(linkedin_module, "Select", FakeSelect)
    bot = Linkedin.__new__(Linkedin)
    month = {
        "question": "",
        "kind": "select",
        "required": True,
        "value": "Month",
        "options": MONTHS,
    }
    year = {
        "question": "",
        "kind": "select",
        "required": True,
        "value": "Year",
        "options": YEARS,
    }
    start = {
        "question": "Earliest start date?",
        "kind": "text",
        "required": True,
        "value": "",
        "testid": "date-picker-input",
    }
    assert control_shape(month) == "month"
    assert control_shape(year) == "year"
    assert control_shape(start) == "date"
    assert bot.mapped_value_for_field(month) == "March"
    assert bot.mapped_value_for_field(year) == "2019"
    assert bot.mapped_value_for_field(start) == "04/01/2026"
    assert bot.mapped_value_for_field({
        "question": "LinkedIn",
        "kind": "text",
        "required": True,
        "value": "",
    }) == "https://www.linkedin.com/in/example"

    month_el = SelectElement()
    year_el = SelectElement()
    date_el = TextElement()
    assert bot.fill_control({**month, "_element": month_el}, "March") is True
    assert month_el.selected == "March"
    assert bot.fill_control({**year, "_element": year_el}, "2019") is True
    assert year_el.selected == "2019"
    assert bot.fill_control({**start, "_element": date_el}, "04/01/2026") is True
    typed = "".join(
        part
        for key in date_el.keys
        for part in key
        if isinstance(part, str) and part.isascii() and part.isprintable()
    )
    assert "04/01/2026" in typed

    monkeypatch.setattr(config, "earliest_start_date", "", raising=False)
    monkeypatch.setattr(config, "work_history_month", "", raising=False)
    monkeypatch.setattr(config, "work_history_year", "", raising=False)
    assert bot.mapped_value_for_field(month) == ""
    assert bot.mapped_value_for_field(year) == ""
    assert bot.mapped_value_for_field(start) == ""
    assert unanswered_required_reason([month]) == "Unanswered required question: month"
    assert unanswered_required_reason([year]) == "Unanswered required question: year"
    assert "Earliest start date" in unanswered_required_reason([start])
    blank_url = {
        "question": "LinkedIn",
        "kind": "text",
        "required": True,
        "value": "",
    }
    monkeypatch.setattr(config, "LinkedInProfileURL", "", raising=False)
    assert bot.mapped_value_for_field(blank_url) == ""
    assert unanswered_required_reason([blank_url]) == "Unanswered required question: LinkedIn"
