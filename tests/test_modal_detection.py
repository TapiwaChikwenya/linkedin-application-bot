import re
from pathlib import Path

from selenium.common.exceptions import (
    NoSuchElementException,
    NoSuchShadowRootException,
    WebDriverException,
)

from linkedin import Linkedin
from linkedin_easy_apply.modal_detection import (
    DIALOG_SELECTORS,
    INTEROP_HOST_SELECTOR,
    classify_application_action,
    is_application_container_candidate,
    is_carousel_decoy,
)
from linkedin_easy_apply.page_snapshot import FIELD_SELECTOR, field_kind_from_attrs

FIXTURE = Path(__file__).parent / "fixtures" / "easy_apply_modal_sample.html"
BUTTON_TAG = re.compile(r"<button\b([^>]*)>", re.IGNORECASE)
ATTR = re.compile(r'([:@A-Za-z0-9_-]+)\s*=\s*"([^"]*)"')


def _button_attrs(html: str) -> list[dict[str, str]]:
    found = []
    for match in BUTTON_TAG.finditer(html):
        attrs = {key: value for key, value in ATTR.findall(match.group(1))}
        found.append(attrs)
    return found


class FakeElement:
    def __init__(
        self,
        *,
        tag="div",
        attrs=None,
        text="",
        displayed=True,
        enabled=True,
        children=None,
        shadow_children=None,
        parent=None,
    ):
        self.tag_name = tag
        self._attrs = dict(attrs or {})
        self._text = text
        self._displayed = displayed
        self._enabled = enabled
        self._children = list(children or [])
        self._shadow_children = list(shadow_children or [])
        self._parent = parent
        self.id = str(id(self))
        for child in self._children:
            child._parent = self

    @property
    def text(self) -> str:
        parts = [self._text]
        for child in self._children:
            parts.append(child.text)
        return " ".join(part for part in parts if part)

    def get_attribute(self, name: str):
        if name == "class":
            return self._attrs.get("class", "")
        return self._attrs.get(name)

    def is_displayed(self) -> bool:
        return self._displayed

    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def shadow_root(self):
        if not self._shadow_children:
            raise NoSuchShadowRootException("no closed shadow")
        return FakeRoot(self._shadow_children)

    def find_elements(self, by, selector):
        return FakeRoot(self._children).find_elements(by, selector)

    def find_element(self, by, selector):
        if selector == "..":
            if self._parent is None:
                raise NoSuchElementException("no parent")
            return self._parent
        hits = self.find_elements(by, selector)
        if not hits:
            raise NoSuchElementException(selector)
        return hits[0]


class FakeRoot:
    def __init__(self, children):
        self._children = list(children)

    @property
    def text(self) -> str:
        return " ".join(child.text for child in self._children if child.text)

    def find_elements(self, _by, selector):
        matches = []
        for child in self._walk(self._children):
            if _css_match(child, selector):
                matches.append(child)
        return matches

    def _walk(self, nodes):
        for node in nodes:
            yield node
            yield from self._walk(node._children)


def _css_match(element: FakeElement, selector: str) -> bool:
    return any(_css_match_one(element, part.strip()) for part in selector.split(","))


def _css_match_one(element: FakeElement, selector: str) -> bool:
    if not selector or selector == "*":
        return True
    base = re.sub(r":not\([^)]*\)", "", selector).strip()
    ident = ""
    if base.startswith("#"):
        ident, _, rest = base.partition("[")
        ident = ident[1:]
        base = "[" + rest if rest else ""
        if ident and element._attrs.get("id") != ident:
            return False
        if not base:
            return True
    attr_parts = re.findall(r"\[([^\]]+)\]", base)
    tag_part = base[: base.find("[")].strip() if "[" in base else base
    if tag_part.startswith("."):
        classes = (element._attrs.get("class") or "").split()
        if tag_part[1:] not in classes:
            return False
    elif tag_part and tag_part != "*" and tag_part != element.tag_name:
        return False
    for raw in attr_parts:
        if "=" in raw:
            key, value = raw.split("=", 1)
            value = value.strip().strip("'\"")
            if (element._attrs.get(key.strip()) or "") != value:
                return False
        elif raw.strip() not in element._attrs:
            return False
    return True


def _fixture_closed_modal(*, with_dialog_role: bool = True, native_inputs: bool = False) -> FakeElement:
    next_button = FakeElement(
        tag="button",
        attrs={"type": "button", "aria-label": "Next"},
        text="",
    )
    progress = FakeElement(
        tag="div",
        attrs={"role": "progressbar", "aria-valuenow": "0"},
        text="0%",
    )
    combobox = FakeElement(
        tag="div",
        attrs={"role": "combobox", "aria-label": "City"},
    )
    nested_host = FakeElement(
        tag="div",
        attrs={"data-testid": "nested-closed-combobox-host"},
        shadow_children=[combobox],
    )
    children = [
        FakeElement(tag="h1", text="Apply to Example Corp"),
        progress,
        FakeElement(tag="h2", text="Contact info"),
        nested_host,
        FakeElement(tag="footer", children=[next_button]),
    ]
    if native_inputs:
        children.insert(
            3,
            FakeElement(tag="input", attrs={"type": "email", "aria-label": "Email address"}),
        )
    attrs = {"aria-modal": "true", "role": "dialog"} if with_dialog_role else {}
    modal = FakeElement(tag="div", attrs=attrs, children=children)
    host = FakeElement(
        tag="div",
        attrs={"id": "interop-outlet", "data-testid": "interop-shadowdom"},
        shadow_children=[modal],
    )
    return host


def test_fixture_icon_next_is_classified_and_carousel_is_ignored():
    html = FIXTURE.read_text(encoding="utf-8")
    actions = [
        classify_application_action(
            "",
            attrs.get("aria-label", ""),
            attrs.get("data-testid", ""),
            attrs.get("class", ""),
        )
        for attrs in _button_attrs(html)
    ]
    assert "next" in actions
    assert is_carousel_decoy("carousel-inline-right-button")
    carousel = [
        attrs for attrs in _button_attrs(html)
        if attrs.get("data-testid") == "carousel-inline-right-button"
    ]
    assert carousel
    assert classify_application_action(
        "",
        carousel[0]["aria-label"],
        carousel[0]["data-testid"],
        carousel[0].get("class", ""),
    ) == ""
    footer_next = [
        attrs for attrs in _button_attrs(html)
        if attrs.get("aria-label") == "Next" and attrs.get("data-testid") != "carousel-inline-right-button"
    ]
    assert footer_next
    assert classify_application_action("", footer_next[0]["aria-label"]) == "next"


def test_icon_only_next_button_is_classified_from_aria_label():
    assert classify_application_action("", "Next") == "next"
    assert classify_application_action("chevron", "Next") == "next"
    assert classify_application_action("", "Continue to next step") == "next"
    assert classify_application_action("", "Next recommended job") == "next"
    assert classify_application_action("", "Previously viewed job") == ""
    assert classify_application_action(
        "",
        "Next",
        "carousel-inline-right-button",
    ) == ""


def test_semantic_application_container_candidate():
    assert is_application_container_candidate(
        role="dialog",
        text="Apply to Acme",
        field_count=2,
        action_names=("next",),
    )
    assert is_application_container_candidate(
        text="Apply to Example Corp Contact info 0%",
        has_progress=True,
        field_count=0,
        action_names=("next",),
    )
    assert is_application_container_candidate(
        role="dialog",
        aria_modal="true",
        text="Apply to Example Corp",
        field_count=0,
        action_names=("next",),
    )
    assert not is_application_container_candidate(
        text="Recommended jobs",
        field_count=0,
        action_names=("next",),
    )


def test_combobox_and_contenteditable_are_field_targets():
    assert "combobox" in FIELD_SELECTOR
    assert "textbox" in FIELD_SELECTOR
    assert "contenteditable" in FIELD_SELECTOR
    assert field_kind_from_attrs(role="combobox") == "text"
    assert field_kind_from_attrs(role="textbox") == "text"
    assert field_kind_from_attrs(tag="div", contenteditable="true") == "textarea"


def test_linkedin_radio_groups_are_collected_once_by_fieldset():
    assert "fieldset[data-test-form-builder-radio-button-form-component]" in FIELD_SELECTOR
    assert ":not([type='radio'])" in FIELD_SELECTOR
    assert field_kind_from_attrs(tag="fieldset") == "radio"


def test_find_container_uses_shadow_aware_semantic_script_first():
    expected = object()

    class Driver:
        def execute_script(self, _script, selectors):
            assert selectors == list(DIALOG_SELECTORS)
            return expected

        def find_elements(self, _by, selector):
            if "interop-shadowdom" in selector:
                return []
            raise AssertionError("legacy selectors should not run after semantic detection")

    bot = Linkedin.__new__(Linkedin)
    bot.driver = Driver()
    assert bot.findApplicationContainer() is expected
    assert INTEROP_HOST_SELECTOR.startswith("#interop-outlet")


def test_find_action_uses_closed_shadow_footer_next_not_js():
    host = _fixture_closed_modal()
    dialog = host.shadow_root.find_elements("css", "[role='dialog']")[0]
    next_button = dialog.find_elements("css", "button")[0]

    class Driver:
        def execute_script(self, *_args):
            raise AssertionError("closed-shadow Next must not depend on open-shadow JS")

        def find_elements(self, _by, selector):
            raise AssertionError(selector)

    bot = Linkedin.__new__(Linkedin)
    bot.driver = Driver()
    assert bot.findApplicationAction(dialog, ("review", "next")) is next_button


def test_find_container_uses_webdriver_for_closed_interop_shadow_root():
    host = _fixture_closed_modal()
    dialog = host.shadow_root.find_elements("css", "[role='dialog']")[0]

    class Driver:
        def find_elements(self, _by, selector):
            if "interop-shadowdom" in selector:
                return [host]
            raise AssertionError("closed shadow detection should return first")

        def execute_script(self, *_args):
            raise AssertionError("JS must not run when closed shadow already matched")

    bot = Linkedin.__new__(Linkedin)
    bot.driver = Driver()
    assert bot.findApplicationContainer() is dialog


def test_find_container_nested_closed_shadow_without_native_inputs():
    host = _fixture_closed_modal(with_dialog_role=False, native_inputs=False)
    carousel = FakeElement(
        tag="button",
        attrs={"aria-label": "Next", "data-testid": "carousel-inline-right-button"},
    )

    class Driver:
        def find_elements(self, _by, selector):
            if "interop-shadowdom" in selector:
                return [host]
            if selector == "button, [role='button']":
                return [carousel]
            raise AssertionError("light-DOM DIALOG_SELECTORS must not be required")

        def execute_script(self, *_args):
            raise AssertionError("open-shadow JS cannot see this closed tree")

    bot = Linkedin.__new__(Linkedin)
    bot.driver = Driver()
    found = bot.findApplicationContainer()
    assert found is not False
    assert found is not carousel
    action = bot.findApplicationAction(found, ("review", "next"))
    assert action is not None
    assert bot.applicationAction(action) == "next"
    assert bot.applicationAction(carousel) == ""


def test_collect_form_state_pierces_nested_closed_combobox():
    host = _fixture_closed_modal(native_inputs=False)
    dialog = host.shadow_root.find_elements("css", "[role='dialog']")[0]

    class Driver:
        def execute_script(self, *_args):
            raise WebDriverException("closed shadow")

        def find_elements(self, _by, selector):
            if "interop-shadowdom" in selector:
                return [host]
            return []

    bot = Linkedin.__new__(Linkedin)
    bot.driver = Driver()
    fields, snapshot = bot.collect_form_state(dialog)
    kinds_and_questions = {(field["kind"], field["question"]) for field in fields}
    assert ("text", "City") in kinds_and_questions
    assert "Apply to Example Corp" in snapshot
    assert "Contact info" in snapshot


def test_serialize_shadow_markup_dumps_closed_modal_not_page_source():
    host = _fixture_closed_modal()
    dialog = host.shadow_root.find_elements("css", "[role='dialog']")[0]

    class Driver:
        page_source = "<div id='interop-outlet' data-testid='interop-shadowdom'></div>"

        def execute_script(self, script, node=None):
            if node is dialog:
                return "<div role='dialog'>Apply to Example Corp</div>"
            if node is host.shadow_root or node is host:
                return "<div>closed-shadow Contact info 0%</div>"
            return ""

        def find_elements(self, _by, selector):
            if "interop-shadowdom" in selector:
                return [host]
            return []

    bot = Linkedin.__new__(Linkedin)
    bot.driver = Driver()
    markup = bot._serialize_shadow_markup(dialog)
    assert "Apply to Example Corp" in markup
    assert "page_source" not in markup
    assert Driver.page_source not in markup or "Contact info" in markup


def test_progression_failure_keeps_explicit_reason(tmp_path, monkeypatch):
    host = _fixture_closed_modal()
    dialog = host.shadow_root.find_elements("css", "[role='dialog']")[0]
    label = FakeElement(tag="label", text="Email address")
    dialog._children.append(label)

    class Driver:
        page_source = "<html></html>"

        def execute_script(self, script, node=None):
            if "outerHTML" in script or "innerHTML" in script:
                return "<div role='dialog'>Apply to Example Corp</div>"
            return []

        def save_screenshot(self, path):
            Path(path).write_bytes(b"png")

        def find_elements(self, _by, selector):
            if "interop-shadowdom" in selector:
                return [host]
            return []

    bot = Linkedin.__new__(Linkedin)
    bot.driver = Driver()
    monkeypatch.chdir(tmp_path)
    message = bot.applicationFailure(
        dialog,
        "https://www.linkedin.com/jobs/view/1",
        "1",
        "Easy Apply modal was visible but no enabled Next, Review, or Submit action was found",
    )
    assert "modal was visible" in message
    assert "Email address" not in message
    artifacts = list(tmp_path.joinpath("data").glob("easy_apply_failure_1_*_modal.html"))
    assert artifacts
    assert "Apply to Example Corp" in artifacts[0].read_text(encoding="utf-8")
