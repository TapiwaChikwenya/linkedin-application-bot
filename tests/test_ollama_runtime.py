from linkedin_easy_apply.ollama_runtime import OllamaManager, model_is_present


class _FakeProc:
    def __init__(self, pid=4321):
        self.pid = pid
        self._code = None

    def poll(self):
        return self._code


def test_model_is_present_matches_untagged_name():
    assert model_is_present(["llama3.2:latest"], "llama3.2") is True
    assert model_is_present(["mistral:7b"], "llama3.2") is False


def test_ensure_starts_serve_then_reports_ready(tmp_path):
    probes = [
        {"reachable": False, "models": [], "error": "URLError"},
        {"reachable": True, "models": ["llama3.2:latest"], "error": ""},
    ]
    launched = []

    def probe(_host, _timeout=1.0):
        return probes.pop(0) if probes else {"reachable": True, "models": ["llama3.2:latest"], "error": ""}

    def popen(command, **_kwargs):
        launched.append(command)
        return _FakeProc()

    manager = OllamaManager(
        str(tmp_path),
        host="http://127.0.0.1:11434",
        model="llama3.2",
        probe=probe,
        popen=popen,
        which=lambda: r"C:\Ollama\ollama.exe",
        sleep=lambda _dt: None,
        clock=lambda: 0.0,
    )
    result = manager.ensure(wait_seconds=30)
    assert result["ok"] is True
    assert result["ready"] is True
    assert result["started"] is True
    assert launched[0][-1] == "serve"


def test_ensure_pulls_missing_model_in_background(tmp_path):
    launched = []

    def popen(command, **_kwargs):
        launched.append(command)
        return _FakeProc(pid=99)

    manager = OllamaManager(
        str(tmp_path),
        host="http://127.0.0.1:11434",
        model="llama3.2",
        probe=lambda _host, _timeout=1.0: {"reachable": True, "models": [], "error": ""},
        popen=popen,
        which=lambda: "/usr/bin/ollama",
        sleep=lambda _dt: None,
        clock=lambda: 0.0,
    )
    result = manager.ensure()
    assert result["ok"] is True
    assert result["ready"] is False
    assert result["pulling"] is True
    assert "pulling model" in result["detail"]
    assert launched[0][-2:] == ["pull", "llama3.2"]


def test_ensure_reports_missing_binary(tmp_path):
    clock = {"t": 0.0}

    def now():
        return clock["t"]

    def sleep(dt):
        clock["t"] += dt

    manager = OllamaManager(
        str(tmp_path),
        host="http://127.0.0.1:11434",
        model="llama3.2",
        probe=lambda _host, _timeout=1.0: {"reachable": False, "models": [], "error": "URLError"},
        popen=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not spawn")),
        which=lambda: "",
        sleep=sleep,
        clock=now,
    )
    result = manager.ensure(wait_seconds=0)
    assert result["ok"] is False
    assert result["ready"] is False
    assert "not installed" in result["detail"].lower() or "download" in result["detail"].lower()
