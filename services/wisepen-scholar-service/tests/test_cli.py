from offline.cli import build_parser


def test_sync_arxiv_accepts_optional_from_date():
    args = build_parser().parse_args(["sync-arxiv", "--from", "2026-10-20"])

    assert args.command == "sync-arxiv"
    assert args.from_date == "2026-10-20"
