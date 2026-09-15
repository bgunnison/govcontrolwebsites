from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from site_config import SITE_SETTINGS

PROMPTS_PATH = Path(SITE_SETTINGS["prompts_path"])


def load_prompt_config() -> dict[str, Any]:
    prompts_module = None
    if PROMPTS_PATH.exists():
        spec = importlib.util.spec_from_file_location(
            f"govcontrol_active_prompts_{SITE_SETTINGS['key']}",
            PROMPTS_PATH,
        )
        if spec is not None and spec.loader is not None:
            prompts_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(prompts_module)

    last_date = str(getattr(prompts_module, "LAST_DATE", "01/01/2026") if prompts_module else "01/01/2026")
    prompt_guide = str(
        getattr(
            prompts_module,
            "PROMPT_GUIDE",
            "Please research distinct new AI items published after [LAST_DATE] that fit [TOPIC].",
        )
        if prompts_module
        else "Please research distinct new AI items published after [LAST_DATE] that fit [TOPIC]."
    )
    raw_topics = list(getattr(prompts_module, "WEBSITE_TOPICS", []) if prompts_module else [])

    topics: list[dict[str, Any]] = []
    for item in raw_topics:
        if isinstance(item, dict):
            topic = dict(item)
        else:
            topic = {"Topic": str(item), "Prompt": "[PROMPT_GUIDE]"}
        topic_name = str(topic.get("Topic") or topic.get("name") or "").strip()
        if not topic_name:
            continue
        topic["Topic"] = topic_name
        topic["Prompt"] = str(topic.get("Prompt") or topic.get("prompt") or "[PROMPT_GUIDE]")
        topic["LastDate"] = str(topic.get("LastDate") or topic.get("last_date") or last_date)
        topics.append(topic)

    return {
        "last_date": last_date,
        "prompt_guide": prompt_guide,
        "topics": topics,
        "path": PROMPTS_PATH,
    }


def today_prompt_date() -> str:
    return datetime.now().strftime("%m/%d/%Y")


def expand_topic_prompt(prompt_guide: str, topic: dict[str, Any]) -> str:
    topic_name = str(topic.get("Topic") or "").strip()
    last_date = str(topic.get("LastDate") or "")
    guide = prompt_guide.replace("[LAST_DATE]", last_date).replace("[TOPIC]", topic_name)
    prompt = str(topic.get("Prompt") or "[PROMPT_GUIDE]")
    return prompt.replace("[PROMPT_GUIDE]", guide).replace("[LAST_DATE]", last_date).replace("[TOPIC]", topic_name)


def _topic_lines(topic: dict[str, Any]) -> list[str]:
    ordered_keys = ["Topic", "Prompt", "LastDate"]
    extra_keys = [key for key in topic.keys() if key not in ordered_keys]
    lines = ["    {"]
    for key in ordered_keys + extra_keys:
        if key not in topic:
            continue
        lines.append(f"        {key!r}: {topic[key]!r},")
    lines.append("    },")
    return lines


def save_prompt_config(config: dict[str, Any]) -> None:
    path = Path(config.get("path") or PROMPTS_PATH)
    lines = [
        f"LAST_DATE = {str(config.get('last_date', '01/01/2026'))!r}",
        f"PROMPT_GUIDE = {str(config.get('prompt_guide', 'Please research distinct new AI items published after [LAST_DATE] that fit [TOPIC].') )!r}",
        "",
        "",
        "WEBSITE_TOPICS = [",
    ]
    for topic in config.get("topics", []):
        lines.extend(_topic_lines(topic))
    lines.append("]")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def mark_topic_used(config: dict[str, Any], topic_name: str, when: str | None = None) -> None:
    stamp = when or today_prompt_date()
    for topic in config.get("topics", []):
        if str(topic.get("Topic") or "").strip().lower() == topic_name.strip().lower():
            topic["LastDate"] = stamp
            break
    save_prompt_config(config)
