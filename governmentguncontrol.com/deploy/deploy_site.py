from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMON_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(COMMON_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT))

from site_config import SITE_SETTINGS
from ssh_deploy import deploy_directory, deployment_settings, load_private


def main() -> None:
    if not SITE_SETTINGS.get("active", False):
        raise RuntimeError(f"{PROJECT_ROOT.name} is inactive in site_config.py; it was not deployed.")
    settings = deployment_settings(load_private(), PROJECT_ROOT.name)
    result = deploy_directory(PROJECT_ROOT / "dist", settings)
    print(
        f"Deployed {PROJECT_ROOT.name}: {result.uploaded} uploaded, "
        f"{result.unchanged} unchanged, {result.deleted} removed."
    )


if __name__ == "__main__":
    main()
