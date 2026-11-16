from linkedin import Linkedin
from linkedin_easy_apply.search_page import is_result_card_text, select_card_payloads

CARD = (
    "Senior Data Engineer Quantiphi United States (Remote) "
    "Actively reviewing applicants Easy Apply"
)
NESTED = CARD + " extra footer that only the outer card has"


def test_filter_chips_are_not_job_cards():
    assert is_result_card_text("Easy Apply") is False
    assert is_result_card_text("Date posted") is False
    assert is_result_card_text("Experience level") is False


def test_visible_easy_apply_text_is_a_job_card():
    assert is_result_card_text(CARD) is True
    assert is_result_card_text("Short Applied") is False


def test_semantic_layout_wins_over_the_text_fallback():
    semantic = [{"paras": ["Senior Data Engineer"], "text": CARD, "job_id": "1"}]
    broad = [{"paras": ["Other"], "text": CARD, "job_id": ""}]
    assert select_card_payloads({"semantic": semantic, "broad": broad}) == semantic


def test_text_fallback_drops_chips_and_nested_copies():
    chosen = select_card_payloads(
        {
            "semantic": [],
            "broad": [
                {"text": "Date posted", "paras": ["Date posted"]},
                {"text": NESTED, "paras": ["Senior Data Engineer", "Quantiphi"]},
                {"text": CARD, "paras": ["Senior Data Engineer", "Quantiphi"]},
                {
                    "text": (
                        "Data Engineer Acme United States (Remote) Posted 1 week ago Easy Apply"
                    ),
                    "paras": ["Data Engineer", "Acme"],
                },
            ],
        }
    )
    texts = [item["text"] for item in chosen]
    assert CARD not in texts
    assert NESTED in texts
    assert any(text.startswith("Data Engineer Acme") for text in texts)
    assert "Date posted" not in texts


def test_apply_control_uses_accessible_name_when_classes_are_gone():
    class _Button:
        id = "btn-1"
        text = "Easy Apply"
        tag_name = "button"

        def is_displayed(self):
            return True

        def is_enabled(self):
            return True

        def get_attribute(self, name):
            if name == "aria-label":
                return "Easy Apply to this job"
            if name in {"aria-disabled", "disabled", "href", "id", "class"}:
                return ""
            return ""

    class _Driver:
        def find_elements(self, *_args):
            return []

        def execute_script(self, _script, *_args):
            return [_Button()]

    bot = Linkedin.__new__(Linkedin)
    bot.driver = _Driver()
    kind, element = bot.findApplyControl()
    assert kind == "easy_apply"
    assert element is not None
    assert element.tag_name == "button"
