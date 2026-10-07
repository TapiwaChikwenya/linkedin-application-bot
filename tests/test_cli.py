from linkedin_easy_apply.cli import build_parser, validate_configuration


def test_check_flag():
    assert build_parser().parse_args(["--check"]).check is True


def test_login_flag():
    assert build_parser().parse_args(["--login"]).login is True


def test_dashboard_flag():
    assert build_parser().parse_args(["--dashboard"]).dashboard is True


def test_pending_questions_flag():
    args = build_parser().parse_args(["--pending-questions"])
    assert args.pending_questions is True


def test_approve_question_flag():
    args = build_parser().parse_args(["--approve-question", "12", "--answer", "Yes"])
    assert args.approve_question == 12
    assert args.answer == "Yes"


def test_validate_configuration_requires_login(monkeypatch):
    import config

    monkeypatch.setattr(config, "browser", ["Chrome"])
    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    monkeypatch.setattr(config, "email", "")
    monkeypatch.setattr(config, "password", "")
    monkeypatch.setattr(config, "keywords", ["Data Engineer"])
    monkeypatch.setattr(config, "location", ["United States"])
    monkeypatch.setattr(config, "resume_path", "")

    errors = validate_configuration()
    assert any("LINKEDIN_EMAIL" in error for error in errors)


def test_validate_configuration_rejects_missing_resume(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "browser", ["Chrome"])
    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    monkeypatch.setattr(config, "email", "user@example.com")
    monkeypatch.setattr(config, "password", "secret")
    monkeypatch.setattr(config, "keywords", ["Data Engineer"])
    monkeypatch.setattr(config, "location", ["United States"])
    monkeypatch.setattr(config, "resume_path", str(tmp_path / "missing.pdf"))

    errors = validate_configuration()
    assert any("resume file not found" in error for error in errors)


def test_validate_configuration_accepts_existing_resume(monkeypatch, tmp_path):
    import config

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(config, "browser", ["Chrome"])
    monkeypatch.setattr(config, "firefoxProfileRootDir", "")
    monkeypatch.setattr(config, "email", "user@example.com")
    monkeypatch.setattr(config, "password", "secret")
    monkeypatch.setattr(config, "keywords", ["Data Engineer"])
    monkeypatch.setattr(config, "location", ["United States"])
    monkeypatch.setattr(config, "resume_path", str(resume))

    assert validate_configuration() == []


def test_validate_configuration_accepts_firefox_profile(monkeypatch, tmp_path):
    import config

    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    monkeypatch.setattr(config, "browser", ["Firefox"])
    monkeypatch.setattr(config, "firefoxProfileRootDir", str(profile))
    monkeypatch.setattr(config, "email", "")
    monkeypatch.setattr(config, "password", "")
    monkeypatch.setattr(config, "keywords", ["Data Engineer"])
    monkeypatch.setattr(config, "location", ["United States"])
    monkeypatch.setattr(config, "resume_path", "")

    assert validate_configuration() == []


def test_validate_configuration_rejects_missing_firefox_profile(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "browser", ["Firefox"])
    monkeypatch.setattr(config, "firefoxProfileRootDir", str(tmp_path / "missing-profile"))
    monkeypatch.setattr(config, "email", "")
    monkeypatch.setattr(config, "password", "")
    monkeypatch.setattr(config, "keywords", ["Data Engineer"])
    monkeypatch.setattr(config, "location", ["United States"])
    monkeypatch.setattr(config, "resume_path", "")

    errors = validate_configuration()
    assert any("FIREFOX_PROFILE_PATH is not a directory" in error for error in errors)


def test_prepare_local_llm_prints_warmup(monkeypatch, capsys):
    from linkedin_easy_apply.cli import _prepare_local_llm

    monkeypatch.setattr(
        "linkedin_easy_apply.llm.warmup_ollama",
        lambda: {"detail": "Ollama warmup: model loaded"},
    )
    _prepare_local_llm()
    assert "Ollama warmup: model loaded" in capsys.readouterr().out
