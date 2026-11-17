import os

from linkedin_easy_apply.runtime import (
    GECKODRIVER_CACHE_DAYS,
    geckodriver_executable,
    is_profile_locked,
    release_stale_webdriver_lock,
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


def test_release_stale_webdriver_lock_removes_orphaned_file(tmp_path):
    lock = tmp_path / ".wdm-lock-geckodriver-win64"
    lock.write_bytes(b"")
    assert release_stale_webdriver_lock(str(lock)) is True
    assert lock.exists() is False


def test_release_stale_webdriver_lock_missing_file_is_noop(tmp_path):
    assert release_stale_webdriver_lock(str(tmp_path / "missing")) is False


def test_release_stale_webdriver_lock_leaves_a_busy_file(monkeypatch, tmp_path):
    lock = tmp_path / ".wdm-lock-geckodriver-win64"
    lock.write_bytes(b"")
    real_unlink = os.unlink

    def guarded(path):
        if os.path.abspath(path) == os.path.abspath(lock):
            raise PermissionError("in use")
        return real_unlink(path)

    monkeypatch.setattr(os, "unlink", guarded)
    assert release_stale_webdriver_lock(str(lock)) is False
    assert lock.exists() is True


def test_geckodriver_executable_clears_lock_before_install(monkeypatch, tmp_path):
    lock = tmp_path / ".wdm-lock-geckodriver-win64"
    lock.write_bytes(b"")
    calls = {}

    class FakeCache:
        def get_os_type(self):
            return "win64"

        def get_driver_lock_path(self, name, os_type):
            assert name == "geckodriver"
            assert os_type == "win64"
            return str(lock)

    class FakeManager:
        def __init__(self, cache_manager=None):
            calls["cache"] = cache_manager

        def install(self):
            calls["lock_exists_during_install"] = lock.exists()
            return str(tmp_path / "geckodriver.exe")

    def fake_cache(valid_range=1):
        calls["valid_range"] = valid_range
        return FakeCache()

    monkeypatch.setattr(
        "webdriver_manager.core.driver_cache.DriverCacheManager",
        fake_cache,
    )
    monkeypatch.setattr("webdriver_manager.firefox.GeckoDriverManager", FakeManager)
    path = geckodriver_executable()
    assert path.endswith("geckodriver.exe")
    assert calls["valid_range"] == GECKODRIVER_CACHE_DAYS
    assert calls["lock_exists_during_install"] is False
