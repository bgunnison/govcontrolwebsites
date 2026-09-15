from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from site_config import ROOT, SITE_SETTINGS


def run_script(relative_path: str, extra_args: list[str] | None = None) -> None:
    command = [sys.executable, str(ROOT / relative_path), *(extra_args or [])]
    subprocess.run(command, cwd=ROOT, check=True)


def show_status() -> None:
    posts_dir = Path(SITE_SETTINGS["posts_dir"])
    dist_dir = Path(SITE_SETTINGS["dist_dir"])
    posts = len(list(posts_dir.glob("*.json"))) if posts_dir.exists() else 0
    pages = len(list(dist_dir.rglob("index.html"))) if dist_dir.exists() else 0
    state = "active" if SITE_SETTINGS.get("active") else "inactive"
    print(f"{SITE_SETTINGS['site_name']} ({state}): posts={posts}, built_pages={pages}")


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Manage {SITE_SETTINGS['site_name']}.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("build", help="Build the static site into dist/.")
    subparsers.add_parser("update", help="Research new posts and rebuild the site.")
    subparsers.add_parser("test", help="Compile, rebuild, and verify the site.")
    subparsers.add_parser("deploy", help="Test and atomically deploy the configured site.")
    subparsers.add_parser("status", help="Show source and build counts.")
    importer = subparsers.add_parser("import-wordpress", help="Refresh content from the public WordPress API.")
    importer.add_argument("--source", help="Override the configured WordPress base URL.")
    args = parser.parse_args()

    if args.command == "status":
        show_status()
    elif args.command == "build":
        run_script("build/build_site.py")
    elif args.command == "update":
        run_script("update/update_posts.py")
    elif args.command == "test":
        run_script("test/run_tests.py")
    elif args.command == "deploy":
        run_script("test/run_tests.py")
        run_script("deploy/deploy_site.py")
    elif args.command == "import-wordpress":
        extra = ["--source", args.source] if args.source else []
        run_script("update/import_wordpress.py", extra)


if __name__ == "__main__":
    main()
