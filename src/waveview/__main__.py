"""Allow ``python -m waveview`` invocation."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
