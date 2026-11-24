from linkedin_easy_apply.dashboard.bind import (
    is_dashboard_command,
    listening_inodes_from_proc,
    prepare_dashboard_port,
)
from linkedin_easy_apply.worker import posix_kill_order


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


def test_linux_proc_net_tcp_listen_inode_is_not_the_last_column():
    """Linux netstat lines do not end in a PID. /proc/net/tcp state 0A is LISTEN."""
    table = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:2253 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 12345 1 0000000000000000 100 0 0 10 0
   1: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 99999 1 0000000000000000 100 0 0 10 0
   2: 0100007F:2253 0100007F:1234 01 00000000:00000000 00:00000000 00000000     0        0 55555 1 0000000000000000 100 0 0 10 0
"""
    assert listening_inodes_from_proc(table, 8787) == {"12345"}
    assert listening_inodes_from_proc(table, 80) == {"99999"}
    assert listening_inodes_from_proc(table, 443) == set()


def test_posix_stop_signals_the_browser_before_the_worker():
    processes = [
        (10, 1, "python"),
        (20, 10, "geckodriver"),
        (30, 20, "firefox"),
        (99, 1, "other"),
    ]
    assert posix_kill_order(10, processes) == [30, 20, 10]


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
