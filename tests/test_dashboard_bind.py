from linkedin_easy_apply.dashboard.bind import is_dashboard_command, prepare_dashboard_port


def test_dashboard_command_requires_the_dashboard_flag():
    assert is_dashboard_command("python -m linkedin_easy_apply --dashboard") is True
    assert is_dashboard_command("python -m linkedin_easy_apply") is False


def test_free_port_does_not_stop_anything():
    stopped = []
    result = prepare_dashboard_port(
        "127.0.0.1",
        8787,
        probe=lambda _host, _port: False,
        listeners=lambda _port: [111],
        stop_pid=lambda pid: stopped.append(pid),
    )
    assert result["ok"] is True
    assert result["replaced"] == []
    assert stopped == []


def test_previous_dashboard_is_stopped_without_its_worker():
    stopped = []
    open_ports = {"value": True}

    def probe(_host, _port):
        return open_ports["value"]

    def stop(pid):
        stopped.append(pid)
        open_ports["value"] = False

    result = prepare_dashboard_port(
        "127.0.0.1",
        8787,
        probe=probe,
        listeners=lambda _port: [10900],
        command_line=lambda pid: {
            10900: r".venv\Scripts\python.exe -m linkedin_easy_apply --dashboard",
            8316: r".venv\Scripts\python.exe -u -m linkedin_easy_apply --dashboard",
        }.get(pid, "cmd.exe"),
        parent_pid=lambda pid: 8316 if pid == 10900 else 0,
        stop_pid=stop,
        sleep=lambda _dt: None,
    )
    assert result["ok"] is True
    assert stopped == [10900, 8316]
    assert result["replaced"] == [10900, 8316]


def test_foreign_listener_is_left_running():
    stopped = []
    result = prepare_dashboard_port(
        "127.0.0.1",
        8787,
        probe=lambda _host, _port: True,
        listeners=lambda _port: [50],
        command_line=lambda _pid: "nginx",
        stop_pid=lambda pid: stopped.append(pid),
    )
    assert result["ok"] is False
    assert "another program" in str(result["error"])
    assert stopped == []
