from linkedin_easy_apply.runtime import (
    is_profile_locked,
    start_profile_login,
    wait_for_unlocked_profile,
)


def test_missing_lock_file_is_unlocked(tmp_path):
    assert is_profile_locked(str(tmp_path)) is False


def test_readable_lock_file_is_unlocked(tmp_path):
    lock = tmp_path / "parent.lock"
    lock.write_bytes(b"")
    assert is_profile_locked(str(tmp_path)) is False


def test_permission_error_means_locked(monkeypatch, tmp_path):
    lock = tmp_path / "parent.lock"
    lock.write_bytes(b"")
    real_open = open

    def guarded(path, *args, **kwargs):
        if str(path).endswith("parent.lock"):
            raise PermissionError("in use")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded)
    assert is_profile_locked(str(tmp_path)) is True


def test_wait_returns_false_when_not_a_tty(monkeypatch, tmp_path):
    lock = tmp_path / "parent.lock"
    lock.write_bytes(b"")
    monkeypatch.setattr("linkedin_easy_apply.runtime.is_profile_locked", lambda path: True)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert wait_for_unlocked_profile(str(tmp_path), reader=lambda: None, writer=lambda _msg: None) is False


def test_start_profile_login_requires_profile():
    result = start_profile_login("")
    assert result["ok"] is False
    assert "FIREFOX_PROFILE_PATH" in result["error"]


def test_start_profile_login_rejects_missing_directory(tmp_path):
    result = start_profile_login(str(tmp_path / "missing"))
    assert result["ok"] is False
    assert "not a directory" in result["error"]


def test_start_profile_login_rejects_locked_profile(monkeypatch, tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setattr("linkedin_easy_apply.runtime.is_profile_locked", lambda _path: True)
    result = start_profile_login(str(profile))
    assert result["ok"] is False
    assert "locked" in result["error"].lower()


def test_start_profile_login_opens_firefox(monkeypatch, tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    captured = {}
    monkeypatch.setattr(
        "linkedin_easy_apply.runtime.firefox_executable",
        lambda: r"C:\Fake\firefox.exe",
    )
    monkeypatch.setattr("linkedin_easy_apply.runtime.is_profile_locked", lambda _path: False)

    def fake_popen(command):
        captured["command"] = command

    monkeypatch.setattr("linkedin_easy_apply.runtime.subprocess.Popen", fake_popen)
    result = start_profile_login(str(profile))
    assert result["ok"] is True
    assert "Sign in" in result["message"]
    assert captured["command"][0].endswith("firefox.exe")
    assert "-no-remote" in captured["command"]
    assert "-profile" in captured["command"]
    assert str(profile) in captured["command"]
    assert "https://www.linkedin.com/login" in captured["command"]
