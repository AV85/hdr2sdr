"""Entry point: `python -m hdr2sdr` or the `hdr2sdr` command."""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("--cli", "-c"):
        from .cli import main as cli_main
        sys.argv.pop(1)
        return cli_main()
    try:
        from .gui import run
    except ImportError as e:
        print("PySide6 is required for the GUI (pip install PySide6). "
              "You can still use `hdr2sdr-cli`.\n" + str(e), file=sys.stderr)
        return 1
    return run()


if __name__ == "__main__":
    sys.exit(main())
