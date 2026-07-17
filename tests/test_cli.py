from linkedin_easy_apply.cli import build_parser


def test_check_flag():
    assert build_parser().parse_args(["--check"]).check is True
