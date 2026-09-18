"""Command-line interface for the application assistant."""

from __future__ import annotations

import argparse
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linkedin-easy-apply",
        description="Run the browser-based LinkedIn Easy Apply assistant.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration without opening a browser.",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Open the configured Firefox profile at LinkedIn login, then exit.",
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Open the local operator dashboard on 127.0.0.1:8787.",
    )
    parser.add_argument(
        "--pending-questions",
        action="store_true",
        help="List screening questions waiting for an operator-approved answer.",
    )
    parser.add_argument(
        "--approve-question",
        type=int,
        metavar="ID",
        help="Approve a stored screening question by id.",
    )
    parser.add_argument(
        "--answer",
        default="",
        help="Approved answer used with --approve-question.",
    )
    return parser


def validate_configuration() -> list[str]:
    import config
    from linkedin_easy_apply.operator_settings import resolved_resume_path

    errors: list[str] = []
    if not config.browser or config.browser[0].lower() not in {"firefox", "chrome"}:
        errors.append("browser must contain Firefox or Chrome")
    profile = config.firefoxProfileRootDir
    if profile and not os.path.isdir(profile):
        errors.append("FIREFOX_PROFILE_PATH is not a directory: " + profile)
    if not profile and not (config.email and config.password):
        errors.append(
            "set FIREFOX_PROFILE_PATH or both LINKEDIN_EMAIL and LINKEDIN_PASSWORD"
        )
    if not config.keywords:
        errors.append("at least one job keyword is required")
    if not config.location:
        errors.append("at least one job location is required")
    resume_path = resolved_resume_path() or getattr(config, "resume_path", "")
    if resume_path and not os.path.isfile(resume_path):
        errors.append("resume file not found: " + resume_path)
    return errors


def _enable_live_logs() -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass


def _print_startup_banner() -> None:
    import config
    from linkedin_easy_apply.pacing import normalize_pace
    from linkedin_easy_apply.quota import applications_today, remaining_applications

    pace = normalize_pace(getattr(config, "pace", "human"))
    run_cap = int(getattr(config, "max_applications_per_run", 12))
    day_cap = int(getattr(config, "max_applications_per_day", 25))
    remaining = remaining_applications(0, run_cap, day_cap)
    print("LinkedIn Easy Apply")
    print("Pace: " + pace)
    print(
        "Application caps: "
        + str(run_cap)
        + " this run, "
        + str(day_cap)
        + " today ("
        + str(applications_today())
        + " already recorded today, "
        + str(remaining)
        + " remaining)."
    )
    print("Leave the browser window visible. Press Ctrl+C in this window to stop.")
    print("Close other Firefox windows that use this bot profile before continuing.")
    from linkedin_easy_apply.llm import status as llm_status

    info = llm_status()
    print("Local LLM: " + str(info.get("model") or "") + " — " + str(info.get("detail") or ""))


def _prepare_firefox_profile() -> int:
    import config
    from linkedin_easy_apply.runtime import wait_for_unlocked_profile

    profile = config.firefoxProfileRootDir
    if not profile:
        return 0
    if wait_for_unlocked_profile(profile):
        return 0
    print(
        "Configuration error: Firefox is using FIREFOX_PROFILE_PATH. "
        "Close that Firefox window, then Start run from the dashboard.",
        file=sys.stderr,
    )
    return 2


def _open_login() -> int:
    import config
    from linkedin_easy_apply.runtime import start_profile_login

    result = start_profile_login(config.firefoxProfileRootDir)
    if not result.get("ok"):
        print("Configuration error: " + str(result.get("error") or "login failed"), file=sys.stderr)
        return 2
    print(str(result.get("message") or "Firefox opened at LinkedIn login."))
    return 0


def _run_question_commands(args: argparse.Namespace) -> int | None:
    pending = bool(getattr(args, "pending_questions", False))
    approve_id = getattr(args, "approve_question", None)
    if not pending and approve_id is None:
        return None
    from linkedin_easy_apply.store import Store, default_store_path

    store = Store(default_store_path())
    try:
        if approve_id is not None:
            answer = str(getattr(args, "answer", "") or "").strip()
            if not answer:
                print("Configuration error: --answer is required with --approve-question", file=sys.stderr)
                return 2
            try:
                row = store.approve_answer(approve_id, answer)
            except KeyError:
                print("Configuration error: unknown question id " + str(approve_id), file=sys.stderr)
                return 2
            print("Approved #" + str(row["id"]) + ": " + row["raw_question"])
        if pending:
            rows = store.list_pending_questions()
            if not rows:
                print("No pending screening questions.")
                return 0
            print("id\tseen\tsource\tkind\tquestion\tproposed")
            for row in rows:
                print(
                    str(row["id"])
                    + "\t"
                    + str(row["seen_count"])
                    + "\t"
                    + str(row["source"])
                    + "\t"
                    + str(row["field_kind"])
                    + "\t"
                    + str(row["raw_question"])
                    + "\t"
                    + str(row["proposed_value"] or "")
                )
        return 0
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    _enable_live_logs()
    args = build_parser().parse_args(argv)
    question_status = _run_question_commands(args)
    if question_status is not None:
        return question_status
    if args.dashboard:
        from linkedin_easy_apply.dashboard.app import run_dashboard

        return run_dashboard()
    from linkedin_easy_apply.operator_settings import apply_operator_overrides

    import config as config_module

    apply_operator_overrides(config_module)
    errors = validate_configuration()
    if errors:
        for error in errors:
            print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    if args.check:
        from linkedin_easy_apply.llm import status as llm_status

        print("Configuration is valid.")
        info = llm_status()
        print("Local LLM: " + str(info.get("model") or "") + " — " + str(info.get("detail") or ""))
        return 0
    if args.login:
        return _open_login()

    from linkedin_easy_apply.worker import worker_process_lease

    # Claim the long-lived interpreter PID before importing selenium/linkedin.py.
    with worker_process_lease():
        _print_startup_banner()
        profile_status = _prepare_firefox_profile()
        if profile_status:
            return profile_status
        from linkedin import main as run_bot

        return run_bot()
