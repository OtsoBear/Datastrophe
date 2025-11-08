import sys


def _message() -> str:
    return (
        "This project uses youtube_poc.py as the entry point for the demo.\n"
        "Recommended (uv):\n"
        "  # install uv (macOS): brew install uv\n"
        "  uv sync            # creates .venv from pyproject.toml\n"
        "  uv run youtube_poc.py --show\n"
        "\n"
        "Alternative (pip):\n"
        "  pip install -r requirements.txt\n"
        "  python youtube_poc.py --show\n"
    )


if __name__ == "__main__":
    sys.stdout.write(_message())

