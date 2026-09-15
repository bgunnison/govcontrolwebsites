from __future__ import annotations

import compileall
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build.build_site import build_site
from site_config import SITE_SETTINGS
from test.test_update_resilience import verify_update_resilience
from test.test_publication_dates import verify_publication_dates
from test.verify_site import verify_site


def main() -> None:
    if not compileall.compile_dir(ROOT, quiet=1):
        raise RuntimeError("Python source compilation failed.")
    verify_update_resilience()
    verify_publication_dates()
    output = build_site()
    result = verify_site()
    print(
        f"Tested {SITE_SETTINGS['site_name']} in {output}: {result['posts']} posts, "
        f"{result['categories']} categories, {result['html_pages']} HTML pages, "
        f"{result['assets']} assets."
    )


if __name__ == "__main__":
    main()
