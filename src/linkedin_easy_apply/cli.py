"""Command-line interface for the application assistant."""

from __future__ import annotations

import argparse
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
    return parser


def validate_configuration() -> list[str]:
    import config

    errors: list[str] = []
    if not config.browser or config.browser[0].lower() not in {"firefox", "chrome"}:
        errors.append("browser must contain Firefox or Chrome")
    if not config.firefoxProfileRootDir and not (config.email and config.password):
        errors.append(
            "set FIREFOX_PROFILE_PATH or both LINKEDIN_EMAIL and LINKEDIN_PASSWORD"
        )
    if not config.keywords:
        errors.append("at least one job keyword is required")
    if not config.location:
        errors.append("at least one job location is required")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_configuration()
    if errors:
        for error in errors:
            print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    if args.check:
        print("Configuration is valid.")
        return 0

    from linkedin import main as run_bot

    return run_bot()
