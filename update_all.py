from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECTS = (
    ROOT / "governmentaicontrol.com",
    ROOT / "governmentguncontrol.com",
    ROOT / "governmenthealthcarecontrol.com",
)
SITE_COOLDOWN_SECONDS = 10
DEFAULT_MAX_UPDATE_COST_USD = 5.00


def load_private_environment() -> dict[str, str]:
    environment = os.environ.copy()
    private_path = ROOT / "private.py"
    if not private_path.exists():
        return environment
    spec = importlib.util.spec_from_file_location("govcontrol_private", private_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {private_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    api_key = str(getattr(module, "OPENAI_API_KEY", "") or "").strip()
    if api_key:
        environment["OPENAI_API_KEY"] = api_key
    configured_limit = getattr(module, "OPENAI_MAX_UPDATE_COST_USD", None)
    if configured_limit is not None:
        environment["OPENAI_MAX_UPDATE_COST_USD"] = str(float(configured_limit))
    return environment


def cost_ledger_total(path: Path) -> float:
    if not path.is_file():
        return 0.0
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            total += float(record.get("estimated_cost_usd") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return total


def latest_update_error(project: Path) -> str:
    log_path = project / "logs" / "generate_log.jsonl"
    if not log_path.is_file():
        return "See the site updater output above for details."
    error_events = {
        "quota_error",
        "cost_limit_deferred",
        "rate_limit_deferred",
        "research_error",
        "summary_error",
    }
    for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("event") in error_events:
            return str(record.get("reason") or record.get("event"))
    return "See the site updater output above for details."


def run(project: Path, relative_script: str, environment: dict[str, str]) -> None:
    command = [sys.executable, str(project / relative_script)]
    print(f"\n[{project.name}] {relative_script}", flush=True)
    subprocess.run(command, cwd=project, env=environment, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update, build, and verify all Government Control sites.")
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Skip news generation and only rebuild and verify all sites.",
    )
    args = parser.parse_args()
    environment = load_private_environment()
    environment.setdefault("OPENAI_MAX_UPDATE_COST_USD", str(DEFAULT_MAX_UPDATE_COST_USD))
    cost_ledger = ROOT / "logs" / (
        "api_cost_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".jsonl"
    )
    environment["OPENAI_COST_LEDGER_PATH"] = str(cost_ledger)

    missing = [str(project) for project in PROJECTS if not project.is_dir()]
    if missing:
        raise RuntimeError("Missing site project(s): " + ", ".join(missing))

    update_failure: str | None = None
    update_failure_reason: str | None = None
    if not args.build_only:
        if not environment.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY is missing. Add it to private.py or the environment before running update.bat."
            )
        print(
            f"OpenAI estimated-cost stop limit for this update: "
            f"${float(environment['OPENAI_MAX_UPDATE_COST_USD']):.2f}",
            flush=True,
        )
        for index, project in enumerate(PROJECTS):
            if index:
                print(f"\nWaiting {SITE_COOLDOWN_SECONDS} seconds before the next site...", flush=True)
                time.sleep(SITE_COOLDOWN_SECONDS)
            try:
                run(project, "update/update_site.py", environment)
            except subprocess.CalledProcessError:
                update_failure = project.name
                update_failure_reason = latest_update_error(project)
                print(
                    f"\n[{project.name}] update paused or failed: {update_failure_reason}\n"
                    "Remaining site updates were deferred to avoid more API requests.",
                    file=sys.stderr,
                    flush=True,
                )
                break

    build_failures: list[str] = []
    for project in PROJECTS:
        try:
            run(project, "test/run_tests.py", environment)
        except subprocess.CalledProcessError:
            build_failures.append(project.name)

    if not args.build_only:
        print(f"\nEstimated OpenAI API cost recorded for this update: ${cost_ledger_total(cost_ledger):.4f}")

    if update_failure or build_failures:
        if update_failure:
            print(
                f"\nUpdate is incomplete at {update_failure}.\n"
                f"Reason: {update_failure_reason}\n"
                "Completed work was saved and all buildable sites were rebuilt.",
                file=sys.stderr,
            )
        if build_failures:
            print("Build or verification failed for: " + ", ".join(build_failures), file=sys.stderr)
        raise SystemExit(1)

    if args.build_only:
        print("\nAll sites are built, verified, and ready to deploy.")
    else:
        print("\nAll sites are updated, built, verified, and ready to deploy.")


if __name__ == "__main__":
    main()
