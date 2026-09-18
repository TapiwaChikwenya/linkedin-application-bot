from pathlib import Path

from linkedin import Linkedin
from linkedin_easy_apply.job_card import (
    APPLY_KIND_EASY,
    APPLY_KIND_MISSING,
    SKIP_ALREADY_APPLIED,
    SKIP_NO_EASY_APPLY,
    SKIP_TITLE_FILTER,
    SKIP_UNKNOWN_CARD,
    SearchCard,
    apply_card_fallbacks,
    card_skip_decision,
    jobs_allowed_to_open,
    normalize_job_id,
    parse_aria_label,
    parse_search_card,
    parse_search_cards,
)

FIXTURE = Path(__file__).parent / "fixtures" / "job_search_cards.html"


def test_normalize_job_id_accepts_urn_and_url():
    assert normalize_job_id("4461000001") == "4461000001"
    assert normalize_job_id("urn:li:jobPosting:4461000002") == "4461000002"
    assert normalize_job_id("https://www.linkedin.com/jobs/view/4461000003/?trk=x") == "4461000003"


def test_aria_label_fallback_parses_title_and_company():
    assert parse_aria_label("with verification Senior Data Engineer .") == (
        "Senior Data Engineer",
        "",
    )
    assert parse_aria_label("Payroll Clerk at TempShop") == ("Payroll Clerk", "TempShop")
    assert parse_aria_label("Easy Apply to Data Platform Engineer at Initech") == (
        "Data Platform Engineer",
        "Initech",
    )
    assert parse_aria_label("Easy Apply") == ("", "")


def test_fixture_cards_expose_title_company_and_easy_apply():
    cards = {card.job_id: card for card in parse_search_cards(FIXTURE.read_text(encoding="utf-8"))}
    easy = cards["4461000001"]
    assert easy.title == "Senior Data Engineer"
    assert easy.company == "Acme Analytics"
    assert easy.apply_kind == APPLY_KIND_EASY
    intern = cards["4461000002"]
    assert intern.title == "Data Engineer Intern"
    assert intern.apply_kind == APPLY_KIND_EASY
    assert cards["4461000008"].title == "Payroll Clerk"
    assert cards["4461000008"].company == "TempShop"
    assert cards["4461000009"].title == ""
    assert cards["4461000010"].apply_kind == APPLY_KIND_EASY


def test_list_side_matching_does_not_open_junk(monkeypatch):
    import config

    monkeypatch.setattr(config, "blacklist", ["EPAM Anywhere"])
    monkeypatch.setattr(config, "blackListTitles", ["intern", "staffing"])
    monkeypatch.setattr(config, "onlyApply", [""])
    monkeypatch.setattr(
        config,
        "onlyApplyTitles",
        ["data engineer", "data platform", "etl", "sql developer"],
    )
    cards = parse_search_cards(FIXTURE.read_text(encoding="utf-8"))
    allowed = jobs_allowed_to_open(cards, applied_ids={"4461000007"})
    assert [card.job_id for card in allowed] == ["4461000001", "4461000010"]

    by_id = {card.job_id: card for card in cards}
    assert card_skip_decision(by_id["4461000002"]) == ("skipped_filter", SKIP_TITLE_FILTER)
    assert card_skip_decision(by_id["4461000003"]) == ("skipped_filter", SKIP_TITLE_FILTER)
    assert card_skip_decision(by_id["4461000004"]) == ("skipped_filter", SKIP_TITLE_FILTER)
    assert card_skip_decision(by_id["4461000005"]) == ("skipped_filter", SKIP_TITLE_FILTER)
    assert card_skip_decision(by_id["4461000006"]) == ("failed", SKIP_NO_EASY_APPLY)
    assert card_skip_decision(by_id["4461000007"]) == ("already_applied", SKIP_ALREADY_APPLIED)
    assert card_skip_decision(by_id["4461000008"]) == ("skipped_filter", SKIP_TITLE_FILTER)
    assert card_skip_decision(by_id["4461000009"]) == ("skipped_filter", SKIP_UNKNOWN_CARD)
    assert card_skip_decision(by_id["4461000011"])[0] == "failed"


def test_store_applied_skips_without_opening_matching_title(monkeypatch):
    import config

    monkeypatch.setattr(config, "onlyApplyTitles", ["data engineer"])
    monkeypatch.setattr(config, "blackListTitles", [])
    monkeypatch.setattr(config, "blacklist", [])
    card = SearchCard(
        job_id="4461000001",
        title="Senior Data Engineer",
        company="Acme Analytics",
        apply_kind=APPLY_KIND_EASY,
    )
    assert card_skip_decision(card, applied_in_store=True) == (
        "already_applied",
        SKIP_ALREADY_APPLIED,
    )


def test_empty_card_uses_container_attributes_not_unknown_open():
    html = """
    <li data-occludable-job-id="4461999999">
      <div class="job-card-container" data-job-id="4461999999"></div>
    </li>
    """
    card = parse_search_card(html, "4461999999")
    assert card.title == ""
    assert card_skip_decision(card) == ("skipped_filter", SKIP_UNKNOWN_CARD)
    filled = apply_card_fallbacks(
        card,
        aria_labels=["Senior Data Engineer at Hidden Corp"],
        extra_class="job-card-container__apply-method",
        extra_text="Easy Apply",
        extra_job_id="4461999999",
    )
    assert filled.title == "Senior Data Engineer"
    assert filled.company == "Hidden Corp"
    assert filled.apply_kind == APPLY_KIND_EASY
    assert card_skip_decision(filled) is None


def test_occluded_card_without_metadata_is_not_opened():
    card = SearchCard(job_id="4461000099", title="", company="", apply_kind=APPLY_KIND_MISSING)
    assert card_skip_decision(card) == ("skipped_filter", SKIP_UNKNOWN_CARD)


def test_should_skip_job_wrapper_matches_card_filter(monkeypatch):
    import config

    monkeypatch.setattr(config, "blacklist", ["EPAM Anywhere"])
    monkeypatch.setattr(config, "blackListTitles", ["intern", "junior"])
    monkeypatch.setattr(config, "onlyApply", [""])
    monkeypatch.setattr(config, "onlyApplyTitles", ["data engineer", "sql developer"])
    assert Linkedin.shouldSkipJob("Junior Data Engineer", "Acme") is True
    assert Linkedin.shouldSkipJob("Senior Data Engineer", "Acme") is False
    assert Linkedin.shouldSkipJob("", "Acme") is False


class _FakeDriver:
    def execute_script(self, *args, **kwargs):
        return None

    def get(self, url):
        raise AssertionError("search-card skip must not open " + str(url))


class _FakeOffer:
    def __init__(self, html: str, job_id: str):
        self._html = html
        self._job_id = job_id

    def get_attribute(self, name: str):
        if name == "outerHTML":
            return self._html
        if name in {"data-occludable-job-id", "data-job-id"}:
            return self._job_id
        if name == "aria-label":
            return ""
        if name == "class":
            return "scaffold-layout__list-item"
        return ""

    @property
    def text(self) -> str:
        return "Data Engineer Intern Acme Analytics Easy Apply"

    def find_elements(self, *args, **kwargs):
        return []


def test_inspect_search_card_skips_store_applied_without_dom(tmp_path):
    from linkedin_easy_apply.store import Store

    store = Store(str(tmp_path / "assistant.sqlite"))
    store.upsert_job(
        "4461000001",
        title="Senior Data Engineer",
        company="Acme Analytics",
        status="applied",
        url="https://www.linkedin.com/jobs/view/4461000001",
    )
    bot = Linkedin.__new__(Linkedin)
    bot.store = store
    bot.driver = _FakeDriver()

    class BoomOffer(_FakeOffer):
        def get_attribute(self, name: str):
            if name == "data-occludable-job-id":
                return "4461000001"
            raise AssertionError("store-applied skip must not read card HTML")

    card, skip = bot.inspect_search_card(BoomOffer("", "4461000001"))
    assert card.source == "store"
    assert skip == ("already_applied", SKIP_ALREADY_APPLIED)


def test_inspect_search_card_skips_intern_without_opening(monkeypatch):
    import config

    monkeypatch.setattr(config, "blackListTitles", ["intern", "staffing"])
    monkeypatch.setattr(config, "onlyApplyTitles", ["data engineer"])
    monkeypatch.setattr(config, "blacklist", [])
    monkeypatch.setattr(config, "onlyApply", [""])
    intern_html = """
    <li data-occludable-job-id="4461000002">
      <div class="job-card-container" data-job-id="4461000002">
        <a class="job-card-list__title--link" href="/jobs/view/4461000002/">
          <strong>Data Engineer Intern</strong>
        </a>
        <span class="job-card-container__primary-description">Acme Analytics</span>
        <span class="job-card-container__apply-method">Easy Apply</span>
      </div>
    </li>
    """
    bot = Linkedin.__new__(Linkedin)
    bot.store = None
    bot.driver = _FakeDriver()
    card, skip = bot.inspect_search_card(_FakeOffer(intern_html, "4461000002"))
    assert card.title == "Data Engineer Intern"
    assert skip == ("skipped_filter", SKIP_TITLE_FILTER)
