"""Module entry point that still refuses non-frozen execution."""

from pc_manager_agent.broker.main import main

raise SystemExit(main())
