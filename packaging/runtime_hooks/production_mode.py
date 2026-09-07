"""PyInstaller-only hook that cannot be overridden by the caller's environment."""

import os

os.environ["PC_MANAGER_BUILD_MODE"] = "production"
