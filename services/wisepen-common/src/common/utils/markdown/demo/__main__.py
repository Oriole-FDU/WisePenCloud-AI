from .chunking import main as run_chunking
from .outline import main as run_outline
from .parser import main as run_parser


def main() -> None:
    run_parser()
    run_chunking()
    run_outline()


if __name__ == "__main__":
    main()
