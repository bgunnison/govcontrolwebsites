from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build.build_site import build_site
from site_config import GENERATE_SETTINGS, RUN_SETTINGS
from update.update_site import generate_posts_with_settings


def main() -> None:
    if RUN_SETTINGS.get("generate"):
        created = generate_posts_with_settings(GENERATE_SETTINGS)
        print(f"Generated {len(created)} new post(s).")

    if RUN_SETTINGS.get("build"):
        output = build_site()
        print(f"Built site into {output}")


if __name__ == "__main__":
    main()
