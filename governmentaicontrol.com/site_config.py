from __future__ import annotations

import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def _load_private_module():
    private_path = ROOT.parent / "private.py"
    if not private_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("govcontrol_private", private_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


personal = _load_private_module()


def _load_topics(path: Path) -> list[object] | None:
    spec = importlib.util.spec_from_file_location("governmentaicontrol_prompts", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    topics = getattr(module, "WEBSITE_TOPICS", None)
    return list(topics) if isinstance(topics, list) else None


CONTENT_DIR = ROOT / "content"
IMAGES_DIR = CONTENT_DIR / "images"
PROMPTS_PATH = ROOT / "update" / "prompts.py"

SITE_SETTINGS: dict[str, object] = {
    "key": "ai",
    "site_name": "Government AI Control",
    "site_url": "https://governmentaicontrol.com",
    "home_title": "AI Governance and Regulation",
    "logo_alt": "Illustration of an ICE agent detaining a humanoid robot",
    "tagline": "Tracking where automated power meets public power.",
    "description": (
        "Government AI Control is a source-backed archive focused on AI governance, "
        "institutional risk, public oversight, and automated state power."
    ),
    "research_subject": "artificial intelligence governance and government power",
    "content_dir": CONTENT_DIR,
    "posts_dir": CONTENT_DIR / "posts",
    "static_dir": ROOT / "static",
    "dist_dir": ROOT / "dist",
    "prompts_path": PROMPTS_PATH,
    "imported_media_dir": CONTENT_DIR / "imported_media",
    "post_url_prefix": "/posts",
    "category_url_prefix": "/categories",
    "items_per_topic": 16,
    "active": True,
    "media_files": {
        "logo": IMAGES_DIR / "logo.jpg",
        "hero-panel": IMAGES_DIR / "logo.jpg",
        "favicon": IMAGES_DIR / "favicon.svg",
    },
    "media_widths": {"logo": [192, 384], "hero-panel": [640, 832]},
}

WEBSITE_TOPICS = _load_topics(PROMPTS_PATH)

GENERATE_SETTINGS: dict[str, object] = {
    "topics": None,
    "max_topics": 0,
    "items_per_topic": 16,
    "research_model": "gpt-5.4",
    "model": "gpt-5.4",
    "temperature": 0.35,
    "api_key": (getattr(personal, "OPENAI_API_KEY", None) if personal else None) or os.environ.get("OPENAI_API_KEY"),
    "results": 8,
    "source_target": 4,
    "dry_run": False,
    "skip_topics_updated_today": True,
    "request_interval_seconds": 3,
    "max_api_attempts": 6,
    "initial_retry_delay_seconds": 5,
    "max_retry_delay_seconds": 120,
    "max_retry_elapsed_seconds": 600,
    "retry_jitter_seconds": 1.5,
    "max_estimated_cost_usd": float(os.environ.get("OPENAI_MAX_UPDATE_COST_USD", "5.00")),
    "cost_ledger_path": os.environ.get("OPENAI_COST_LEDGER_PATH", ""),
    "log_path": ROOT / "logs" / "generate_log.jsonl",
    "site_subject": SITE_SETTINGS["research_subject"],
}

_deploy = getattr(personal, "DEPLOY_SETTINGS", {}) if personal else {}
DEPLOY_SETTINGS: dict[str, object] = {
    "enabled": bool(_deploy.get("enabled", False)),
    "host": str(_deploy.get("host", "")),
    "user": str(_deploy.get("user", "")),
    "remote_path": str(_deploy.get("remote_path", "")),
    "port": int(_deploy.get("port", 22)),
    "identity_file": str(_deploy.get("identity_file", "")),
    "dry_run": bool(_deploy.get("dry_run", False)),
    "atomic": bool(_deploy.get("atomic", True)),
}

RUN_SETTINGS = {"generate": True, "build": True}
