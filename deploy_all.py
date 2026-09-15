from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

from ssh_deploy import deploy_directory, deployment_settings, load_private

ROOT = Path(__file__).resolve().parent
SITE_NAMES = (
    "governmentaicontrol.com",
    "governmentguncontrol.com",
    "governmenthealthcarecontrol.com",
)


def test_site(site_name: str) -> None:
    project = ROOT / site_name
    print(f"\n[{site_name}] building and verifying", flush=True)
    subprocess.run(
        [sys.executable, str(project / "test" / "run_tests.py")],
        cwd=project,
        check=True,
    )


def site_is_active(site_name: str) -> bool:
    config_path = ROOT / site_name / "site_config.py"
    spec = importlib.util.spec_from_file_location(f"deploy_config_{site_name.replace('.', '_')}", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load site configuration: {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return bool(module.SITE_SETTINGS.get("active", False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build, verify, and SCP-deploy Government Control sites over SSH.")
    parser.add_argument("--site", choices=SITE_NAMES, help="Deploy only one configured site.")
    parser.add_argument("--skip-build", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    private = load_private()
    selected_names = (args.site,) if args.site else SITE_NAMES
    selected = []
    for site_name in selected_names:
        if not site_is_active(site_name):
            if args.site:
                raise RuntimeError(f"{site_name} is inactive in site_config.py; it was not deployed.")
            print(f"[{site_name}] site inactive; skipping deployment.")
            continue
        settings = deployment_settings(private, site_name)
        if settings["enabled"]:
            selected.append((site_name, settings))
        elif args.site:
            raise RuntimeError(f"SSH deployment is disabled for {site_name} in private.py.")
        else:
            print(f"[{site_name}] deployment disabled; skipping.")
    if not selected:
        raise RuntimeError("No SSH sites are enabled in private.py.")

    # Validate every enabled site before the first remote connection.
    for site_name, _ in selected:
        project = ROOT / site_name
        if not project.is_dir():
            raise RuntimeError(f"Site project is missing: {project}")

    if not args.skip_build:
        for site_name, _ in selected:
            test_site(site_name)

    for site_name, settings in selected:
        print(f"\n[{site_name}] deploying with SCP over SSH", flush=True)
        result = deploy_directory(ROOT / site_name / "dist", settings)
        action = "would upload" if result.dry_run else "uploaded"
        initial = (
            " (initial atomic deployment)" if settings.get("initial_atomic", True) else " (initial deployment)"
        ) if result.initial_deploy else ""
        print(
            f"[{site_name}] {action} {result.uploaded} files; "
            f"{result.unchanged} unchanged; {result.deleted} removed{initial}."
        )


if __name__ == "__main__":
    main()
