from scholar.etl.cli import build_parser


def test_sync_arxiv_accepts_optional_from_date():
    args = build_parser().parse_args(["sync-arxiv", "--from", "2026-10-20"])

    assert args.command == "sync-arxiv"
    assert args.from_date == "2026-10-20"


def test_sync_s2_accepts_explicit_base_and_target():
    args = build_parser().parse_args(
        ["sync-s2", "--base", "release-a", "--target", "release-b"]
    )

    assert args.command == "sync-s2"
    assert args.base_release == "release-a"
    assert args.target_release == "release-b"


def test_sync_s2_allows_resume_or_latest_without_release_arguments():
    args = build_parser().parse_args(["sync-s2"])

    assert args.base_release is None
    assert args.target_release is None
