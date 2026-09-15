from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from site_config import SITE_SETTINGS, WEBSITE_TOPICS


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def slugify(value: str) -> str:
    text = value.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "item"


DEFAULT_CATEGORY_LIBRARY: dict[str, dict[str, str]] = {
    "politics": {
        "description": "Executive action, party platforms, agency leadership, elections, and public power around AI.",
        "question": "How is AI becoming a political issue inside government itself?",
        "query": "AI politics government executive agency election policy site:gov OR site:edu OR site:org",
        "accent": "navy",
        "hero_image": "hero-panel",
    },
    "legislation": {
        "description": "Federal and state bills, rulemaking efforts, and formal attempts to regulate AI systems.",
        "question": "What new laws are governments trying to write around AI?",
        "query": "artificial intelligence legislation bill rulemaking government site:gov OR site:edu OR site:org",
        "accent": "red",
        "hero_image": "hero-panel",
    },
    "law": {
        "description": "Courts, administrative law, liability, constitutional questions, and legal accountability for AI.",
        "question": "Where do legal rights and automated systems collide?",
        "query": "artificial intelligence law courts administrative law constitutional government site:gov OR site:edu OR site:org",
        "accent": "steel",
        "hero_image": "hero-panel",
    },
    "risk": {
        "description": "Safety, security, systemic failure, military use, infrastructure exposure, and operational danger.",
        "question": "Which AI systems create public risk faster than institutions can manage it?",
        "query": "artificial intelligence risk safety security critical infrastructure military government site:gov OR site:edu OR site:org",
        "accent": "crimson",
        "hero_image": "hero-panel",
    },
    "privacy": {
        "description": "Surveillance, biometric systems, data collection, identification, and personal autonomy.",
        "question": "What happens when identity and observation become automated?",
        "query": "artificial intelligence privacy biometrics surveillance government site:gov OR site:edu OR site:org",
        "accent": "teal",
        "hero_image": "hero-panel",
    },
    "ethics": {
        "description": "Bias, fairness, labor, accountability, and the human consequences of automated decisions.",
        "question": "What ethical obligations survive once AI is deployed at scale?",
        "query": "artificial intelligence ethics fairness accountability labor government site:gov OR site:edu OR site:org",
        "accent": "gold",
        "hero_image": "hero-panel",
    },
    "opinion": {
        "description": "Interpretive writing, arguments, and public-interest commentary about AI governance.",
        "question": "What should change before AI power becomes routine?",
        "query": "artificial intelligence governance opinion commentary public interest site:gov OR site:edu OR site:org",
        "accent": "stone",
        "hero_image": "hero-panel",
    },
    "activism": {
        "description": "Advocacy groups, campaigns, public pressure, and organized resistance or support around AI policy.",
        "question": "Who is mobilizing the public around AI governance, and why?",
        "query": "artificial intelligence activism advocacy campaign policy government site:gov OR site:edu OR site:org",
        "accent": "green",
        "hero_image": "hero-panel",
    },
    "culture": {
        "description": "Entertainment, celebrity commentary, humor, and the broader cultural mood around AI.",
        "question": "How is AI reshaping popular culture and public imagination?",
        "query": "artificial intelligence culture entertainment celebrity movies humor site:gov OR site:edu OR site:org",
        "accent": "stone",
        "hero_image": "hero-panel",
    },
    "future": {
        "description": "Long-range governance, frontier systems, international trends, and the next phase of public AI power.",
        "question": "What kind of state capacity and control structure is AI building toward?",
        "query": "artificial intelligence future governance frontier systems international standards government site:gov OR site:edu OR site:org",
        "accent": "amber",
        "hero_image": "hero-panel",
    },
}

DEFAULT_ACCENTS = ["navy", "red", "steel", "crimson", "teal", "gold", "stone", "green", "amber"]


def unique_slug(base: str, existing: set[str]) -> str:
    candidate = slugify(base)
    if candidate not in existing:
        return candidate
    idx = 2
    while f"{candidate}-{idx}" in existing:
        idx += 1
    return f"{candidate}-{idx}"


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def format_date(value: str) -> str:
    return parse_date(value).strftime("%B %d, %Y")


def validate_post_dates(posts: list[dict[str, Any]], *, today: str | None = None) -> None:
    """Reject invalid/future publication metadata before writing or building posts."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    for post in posts:
        label = post.get("_source_path") or post.get("slug") or "<untitled>"
        fields = {"published_at": post.get("published_at")}
        for key in ("updated_at", "created_at"):
            if key in post:
                fields[key] = post[key]
        for index, source in enumerate(post.get("sources", [])):
            if source.get("published_at"):
                fields[f"sources[{index}].published_at"] = source["published_at"]
        for field, value in fields.items():
            try:
                valid = (
                    isinstance(value, str)
                    and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None
                    and parse_date(value).strftime("%Y-%m-%d") == value
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"{label}: invalid {field}: {value!r}; expected YYYY-MM-DD")
            if value > today:
                raise ValueError(f"{label}: future {field}: {value} (today is {today})")


def estimate_reading_time(sections: list[dict[str, Any]]) -> int:
    words = 0
    for section in sections:
        words += len(" ".join(section.get("paragraphs", [])).split())
    return max(2, round(words / 220))


def category_from_topic(topic: Any, index: int) -> dict[str, str]:
    if isinstance(topic, dict):
        raw = dict(topic)
    else:
        raw = {"name": str(topic)}

    name = str(raw.get("name") or raw.get("Topic") or raw.get("slug") or f"Topic {index + 1}").strip()
    slug = slugify(str(raw.get("slug") or name))
    template = DEFAULT_CATEGORY_LIBRARY.get(slug, {})

    return {
        "name": name,
        "slug": slug,
        "description": str(
            raw.get("description")
            or template.get("description")
            or f"Public-interest coverage of {name.lower()} and government uses of AI."
        ),
        "question": str(
            raw.get("question")
            or template.get("question")
            or f"How is government AI changing {name.lower()}?"
        ),
        "query": str(
            raw.get("query")
            or template.get("query")
            or f"{name} artificial intelligence government policy oversight site:gov OR site:edu OR site:org"
        ),
        "accent": str(raw.get("accent") or template.get("accent") or DEFAULT_ACCENTS[index % len(DEFAULT_ACCENTS)]),
        "hero_image": str(raw.get("hero_image") or template.get("hero_image") or "hero-panel"),
    }


def categories_from_topics(topics: list[Any]) -> list[dict[str, str]]:
    categories: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, topic in enumerate(topics):
        category = category_from_topic(topic, index)
        if category["slug"] in seen:
            continue
        seen.add(category["slug"])
        categories.append(category)
    return categories


def load_categories() -> list[dict[str, Any]]:
    path = Path(SITE_SETTINGS["content_dir"]) / "categories.json"
    if WEBSITE_TOPICS:
        generated = categories_from_topics(list(WEBSITE_TOPICS))
        existing = load_json(path) if path.exists() else None
        generated_slugs = {item["slug"] for item in generated}
        # Preserve import-only archives (for example WordPress's historical
        # Uncategorized bucket) without adding them to periodic research.
        if isinstance(existing, list):
            generated.extend(
                item
                for item in existing
                if isinstance(item, dict) and item.get("slug") not in generated_slugs
            )
        if existing != generated:
            write_json(path, generated)
        return generated
    return load_json(path)


def load_site_content() -> dict[str, Any]:
    return load_json(Path(SITE_SETTINGS["content_dir"]) / "site.json")


def load_posts() -> list[dict[str, Any]]:
    posts_dir = Path(SITE_SETTINGS["posts_dir"])
    posts: list[dict[str, Any]] = []
    for path in sorted(posts_dir.glob("*.json")):
        post = load_json(path)
        post["_source_path"] = str(path)
        posts.append(post)
    posts.sort(key=lambda item: item.get("published_at", ""), reverse=True)
    return posts


def ensure_unique_post_slugs(posts: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for post in posts:
        slug = post.get("slug", "")
        if not slug:
            raise ValueError(f"Post is missing slug: {post.get('title', '<untitled>')}")
        if slug in seen:
            raise ValueError(f"Duplicate post slug detected: {slug}")
        seen.add(slug)


def category_lookup(categories: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["slug"]: item for item in categories}


def category_post_counts(posts: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for post in posts:
        counts[post["category"]] = counts.get(post["category"], 0) + 1
    return counts


def html_text(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
