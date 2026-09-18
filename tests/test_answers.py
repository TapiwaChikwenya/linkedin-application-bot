from linkedin import Linkedin


def test_experience_question_mapping():
    assert Linkedin.yesNoAnswerForQuestion(
        "Are you legally authorized to work in the United States?"
    ) == "Yes"
    assert Linkedin.yesNoAnswerForQuestion(
        "Will you now or in the future require sponsorship?"
    ) == "No"


def test_unknown_yes_no_question_is_not_guessed():
    assert Linkedin.yesNoAnswerForQuestion("What is your favorite color?") is None


def test_generic_experience_is_not_yes(monkeypatch):
    import config

    monkeypatch.setattr(
        config,
        "yes_no_answers",
        [(("sponsorship",), "No"), (("experience",), "Yes"), (("years", "experience"), "Yes")],
    )
    assert Linkedin.yesNoAnswerForQuestion("Do you have experience with knitting?") is None
    assert Linkedin.yesNoAnswerForQuestion("Do you have years of experience?") is None
    assert Linkedin.yesNoAnswerForQuestion("Will you now or in the future require sponsorship?") == "No"


def test_application_action_semantics():
    class Button:
        text = "Review"

        @staticmethod
        def get_attribute(name):
            return "" if name == "aria-label" else None

    assert Linkedin.applicationAction(Button()) == "review"


def test_resume_upload_field_detection():
    assert Linkedin.isResumeUploadField("Upload resume (PDF)", "file-resume", "") is True
    assert Linkedin.isResumeUploadField("curriculum vitae", "", "cv") is True
    assert Linkedin.isResumeUploadField("Upload cover letter", "file-cover", "") is False


def test_title_and_company_filters(monkeypatch):
    import config

    monkeypatch.setattr(config, "blacklist", ["EPAM Anywhere"])
    monkeypatch.setattr(config, "blackListTitles", ["intern", "junior"])
    monkeypatch.setattr(config, "onlyApply", [""])
    monkeypatch.setattr(config, "onlyApplyTitles", ["data engineer", "sql developer"])

    assert Linkedin.shouldSkipJob("Junior Data Engineer", "Acme") is True
    assert Linkedin.shouldSkipJob("Senior Data Engineer", "EPAM Anywhere") is True
    assert Linkedin.shouldSkipJob("Warehouse Associate", "Acme") is True
    assert Linkedin.shouldSkipJob("Senior Data Engineer", "Acme") is False
    assert Linkedin.shouldSkipJob("SQL Developer", "Acme") is False
    assert Linkedin.shouldSkipJob("", "Acme") is False


def test_document_title_fallback():
    assert Linkedin.parseDocumentTitle(
        "Senior Data Engineer | Acme | LinkedIn"
    ) == ("Senior Data Engineer", "Acme")
    assert Linkedin.parseDocumentTitle("Feed | LinkedIn") == ("", "")
    assert Linkedin.parseDocumentTitle("LinkedIn Login") == ("", "")
    assert Linkedin.parseDocumentTitle("SQL Developer") == ("SQL Developer", "")


def test_apply_control_classification():
    assert Linkedin.classifyApplyControl(
        "Apply to Lead Data Engineer at Acme",
        element_id="jobs-apply-button-id",
        class_name="jobs-apply-button artdeco-button",
    ) == "easy_apply"
    assert Linkedin.classifyApplyControl("Apply", class_name="jobs-apply-button") == "easy_apply"
    assert Linkedin.classifyApplyControl("LinkedIn Apply to Acme") == "easy_apply"
    assert Linkedin.classifyApplyControl("Easy Apply") == "easy_apply"
    assert Linkedin.classifyApplyControl("", href="https://www.linkedin.com/jobs/view/1?openSDUIApplyFlow=true") == "easy_apply"
    assert Linkedin.classifyApplyControl("Applied") == "already_applied"
    assert Linkedin.classifyApplyControl("You applied on Sep 16") == "already_applied"
    assert Linkedin.classifyApplyControl("Apply on company website") == "external"


def test_search_urls_encode_spaces(monkeypatch):
    import config
    from utils import LinkedinUrlGenerate

    monkeypatch.setattr(config, "location", ["United States"])
    monkeypatch.setattr(config, "keywords", ["Senior Data Engineer"])
    urls = LinkedinUrlGenerate().generateUrlLinks()
    assert urls
    assert "keywords=Senior%20Data%20Engineer" in urls[0]
    assert "location=United%20States" in urls[0]
    assert "Senior Data Engineer" not in urls[0]
    from utils import urlToKeywords
    assert urlToKeywords(urls[0]) == ["Senior Data Engineer", "United States"]
