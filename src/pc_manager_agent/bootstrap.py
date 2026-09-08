"""Lightweight frozen entry that answers metadata requests before importing the GUI graph."""

from __future__ import annotations

import sys

from pc_manager_agent import __version__


def main(argv: list[str] | None = None) -> int:
    """Return the frozen version quickly or delegate all other input to the guarded application."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--version"]:
        if sys.stdout is not None:
            sys.stdout.write(f"{__version__}\n")
        return 0
    from pc_manager_agent.main import main as application_main

    return application_main(arguments)
