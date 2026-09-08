"""Module execution entry point."""

from multiprocessing import freeze_support

from pc_manager_agent.bootstrap import main

if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
