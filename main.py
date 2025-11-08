import sys


def _message() -> str:
    return (
        "This project uses youtube_poc.py as the entry point for the demo.\n"
        "Run:\n"
        "  python youtube_poc.py\n"
        "Optionally create a virtualenv and install requirements first:\n"
        "  python -m venv venv && source venv/bin/activate\n"
        "  pip install -r requirements.txt\n"
    )


if __name__ == "__main__":
    sys.stdout.write(_message())

