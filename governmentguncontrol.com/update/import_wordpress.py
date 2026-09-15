from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from site_config import SITE_SETTINGS
from site_lib import slugify, write_json

USER_AGENT = "GovernmentControlMigration/1.0"
UPLOAD_MARKER = "/wp-content/uploads/"

ALLOWED_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "div",
    "em",
    "figcaption",
    "figure",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "strong",
    "ul",
}
VOID_TAGS = {"br", "hr", "img"}
BLOCKED_TAGS = {"script", "style"}

GUN_CATEGORY_COPY: dict[str, dict[str, str]] = {
    "amendments": {
        "description": "Constitutional amendments, the Second Amendment, and competing interpretations of firearms rights.",
        "question": "How are constitutional rights being interpreted as firearms law changes?",
        "query": "Second Amendment firearms constitutional rights court government",
        "accent": "navy",
    },
    "politics": {
        "description": "Elections, executive action, public officials, parties, and political conflict around firearms.",
        "question": "How is firearms policy shaping political power and public debate?",
        "query": "gun control firearms politics executive election government",
        "accent": "red",
    },
    "religion": {
        "description": "Faith-based arguments, religious advocacy, and moral teaching about guns and violence.",
        "question": "How are faith communities responding to firearms and gun violence?",
        "query": "religion faith gun control firearms violence advocacy",
        "accent": "gold",
    },
    "law": {
        "description": "Court rulings, enforcement, liability, legal doctrine, and the operation of firearms law.",
        "question": "Where do firearms rules, individual rights, and public safety meet in law?",
        "query": "firearms gun control law court ruling enforcement government",
        "accent": "steel",
    },
    "legislation": {
        "description": "Federal, state, and local bills and enacted measures governing firearms.",
        "question": "Which firearms measures are lawmakers proposing or enacting?",
        "query": "gun control firearms legislation bill federal state local",
        "accent": "crimson",
    },
    "opinion": {
        "description": "Arguments, commentary, and competing public perspectives on guns and regulation.",
        "question": "Which values and assumptions drive the firearms debate?",
        "query": "gun control firearms opinion commentary public debate",
        "accent": "stone",
    },
    "activism": {
        "description": "Advocacy organizations, campaigns, protests, and public action around firearms policy.",
        "question": "Who is organizing around gun policy, and what changes do they seek?",
        "query": "gun control firearms activism advocacy campaign protest",
        "accent": "green",
    },
    "ethics": {
        "description": "Moral responsibility, safety, rights, harm, and ethical questions raised by firearms.",
        "question": "What ethical duties should guide firearms ownership and policy?",
        "query": "gun control firearms ethics morality public safety rights",
        "accent": "teal",
    },
    "culture": {
        "description": "Media, history, identity, recreation, and the place of firearms in American culture.",
        "question": "How does culture shape what Americans believe about guns?",
        "query": "guns firearms culture media history identity America",
        "accent": "amber",
    },
    "uncategorized": {
        "description": "Historical posts that were not assigned a topic on the original WordPress site.",
        "question": "Which archived items still need a permanent topic?",
        "query": "gun control firearms government",
        "accent": "stone",
    },
}


def clean_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def local_media_path(url: str) -> PurePosixPath | None:
    decoded = unquote(url)
    marker_index = decoded.lower().find(UPLOAD_MARKER)
    if marker_index < 0:
        return None
    relative = decoded[marker_index + len(UPLOAD_MARKER) :].split("?", 1)[0].split("#", 1)[0]
    candidate = PurePosixPath(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate


def rewrite_media_url(url: str) -> str:
    relative = local_media_path(url)
    if relative is None:
        return url
    return f"/assets/media/imported/{relative.as_posix()}"


class WordPressHTMLSanitizer(HTMLParser):
    def __init__(self, url_rewrites: dict[str, str] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.blocked_depth = 0
        self.url_rewrites = url_rewrites or {}

    def rewrite_url(self, value: str) -> str:
        mapped = self.url_rewrites.get(value.rstrip("/"))
        return mapped or rewrite_media_url(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in BLOCKED_TAGS:
            self.blocked_depth += 1
            return
        if self.blocked_depth or tag not in ALLOWED_TAGS:
            return

        allowed_attrs: list[tuple[str, str]] = []
        for key, raw_value in attrs:
            key = key.lower()
            value = raw_value or ""
            if tag == "a" and key == "href":
                allowed_attrs.append(("href", self.rewrite_url(value)))
            elif tag == "a" and key in {"title"}:
                allowed_attrs.append((key, value))
            elif tag == "img" and key == "src":
                allowed_attrs.append(("src", self.rewrite_url(value)))
            elif tag == "img" and key in {"alt", "title", "width", "height", "loading"}:
                allowed_attrs.append((key, value))
            elif tag in {"div", "figure", "figcaption"} and key == "class":
                safe_classes = " ".join(part for part in value.split() if re.fullmatch(r"[a-zA-Z0-9_-]+", part))
                if safe_classes:
                    allowed_attrs.append(("class", safe_classes))

        if tag == "a":
            href = next((value for key, value in allowed_attrs if key == "href"), "")
            if href.startswith(("http://", "https://")):
                allowed_attrs.extend([("target", "_blank"), ("rel", "noreferrer")])

        attrs_html = "".join(f' {key}="{html.escape(value, quote=True)}"' for key, value in allowed_attrs)
        closing = " /" if tag in VOID_TAGS else ""
        self.output.append(f"<{tag}{attrs_html}{closing}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in BLOCKED_TAGS:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if not self.blocked_depth and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.output.append(html.escape(data, quote=False))

    def result(self) -> str:
        return "".join(self.output).strip()


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.current_href = ""
        self.current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self.current_href = next((value or "" for key, value in attrs if key.lower() == "href"), "")
        self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_href:
            self.links.append((self.current_href, " ".join("".join(self.current_text).split())))
            self.current_href = ""
            self.current_text = []


def sanitize_wordpress_html(value: str, url_rewrites: dict[str, str] | None = None) -> str:
    parser = WordPressHTMLSanitizer(url_rewrites)
    parser.feed(value or "")
    parser.close()
    return parser.result()


def summary_from_html(value: str, fallback: str) -> str:
    text = clean_text(value) or clean_text(fallback)
    text = re.sub(r"\s*Read More\s*[→\-]*\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+Sources?:\s*https?://\S+", "", text, flags=re.IGNORECASE)
    if len(text) <= 900:
        return text
    shortened = text[:900].rsplit(" ", 1)[0].rstrip(".,;:")
    return shortened + "…"


def extract_sources(value: str, site_host: str) -> list[dict[str, str]]:
    collector = LinkCollector()
    collector.feed(value or "")
    collector.close()

    candidates = list(collector.links)
    linked_urls = {url for url, _label in candidates}
    for url in re.findall(r"https?://[^\s<>'\"]+", html.unescape(value or "")):
        cleaned = url.rstrip(").,;:")
        if cleaned not in linked_urls:
            candidates.append((cleaned, ""))

    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for url, label in candidates:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or not host:
            continue
        if local_media_path(url) is not None or host == site_host.removeprefix("www."):
            continue
        normalized = url.rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        title = clean_text(label)
        if not title or title.startswith("http"):
            title = parsed.netloc.removeprefix("www.")
        sources.append({"title": title, "url": url})
    return sources


class WordPressClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch_all(self, resource: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            response = self.session.get(
                f"{self.base_url}/wp-json/wp/v2/{resource}",
                params={"per_page": 100, "page": page},
                timeout=60,
            )
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise RuntimeError(f"Unexpected WordPress response for {resource}")
            items.extend(batch)
            total_pages = int(response.headers.get("X-WP-TotalPages", "1"))
            if page >= total_pages:
                return items
            page += 1

    def download(self, url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(url, timeout=120, stream=True) as response:
            response.raise_for_status()
            with open(target, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        handle.write(chunk)


def category_record(name: str, slug: str) -> dict[str, Any]:
    copy = GUN_CATEGORY_COPY.get(slug, {})
    return {
        "name": name,
        "slug": slug,
        "description": copy.get("description", f"Archived Government Gun Control posts about {name.lower()}."),
        "question": copy.get("question", f"What is changing in {name.lower()}?"),
        "query": copy.get("query", f"gun control firearms {name}"),
        "accent": copy.get("accent", "stone"),
        "hero_image": "hero-panel",
        "nav": slug != "uncategorized",
    }


def import_wordpress(source_url: str | None = None) -> dict[str, Any]:
    settings = SITE_SETTINGS
    base_url = (source_url or str(settings.get("wordpress_url") or "")).rstrip("/")
    if not base_url:
        raise RuntimeError("No WordPress source URL is configured")

    client = WordPressClient(base_url)
    categories = client.fetch_all("categories")
    tags = client.fetch_all("tags")
    pages = client.fetch_all("pages")
    media = client.fetch_all("media")
    posts = client.fetch_all("posts")

    content_dir = Path(settings["content_dir"])
    posts_dir = Path(settings["posts_dir"])
    imported_media_dir = Path(settings["imported_media_dir"])
    images_dir = content_dir / "images"
    posts_dir.mkdir(parents=True, exist_ok=True)
    imported_media_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    downloaded_media = 0
    media_errors: list[dict[str, Any]] = []
    media_url_rewrites: dict[str, str] = {}
    logo_media_id = int(settings.get("wordpress_logo_media_id", 0))
    for item in media:
        source = str(item.get("source_url") or "")
        relative = local_media_path(source)
        if relative is None:
            continue
        target = imported_media_dir.joinpath(*relative.parts)
        local_url = f"/assets/media/imported/{relative.as_posix()}"
        media_url_rewrites[source.rstrip("/")] = local_url
        attachment_url = str(item.get("link") or "").rstrip("/")
        if attachment_url:
            media_url_rewrites[attachment_url] = local_url
        try:
            client.download(source, target)
            downloaded_media += 1
            if int(item.get("id", 0)) == logo_media_id:
                shutil.copy2(target, images_dir / f"logo{target.suffix.lower()}")
        except Exception as exc:
            media_errors.append({"id": item.get("id"), "url": source, "error": str(exc)})

    category_id_to_slug: dict[int, str] = {}
    category_output: list[dict[str, Any]] = []
    seen_category_slugs: set[str] = set()
    for category in sorted(categories, key=lambda item: int(item.get("id", 0))):
        original_slug = slugify(str(category.get("slug") or category.get("name") or "uncategorized"))
        canonical_slug = "law" if original_slug == "laws" else original_slug
        category_id_to_slug[int(category["id"])] = canonical_slug
        if canonical_slug in seen_category_slugs:
            continue
        seen_category_slugs.add(canonical_slug)
        category_output.append(category_record(clean_text(str(category.get("name") or canonical_slug.title())), canonical_slug))
    write_json(content_dir / "categories.json", category_output)

    tag_names = {int(item["id"]): clean_text(str(item.get("name") or "")) for item in tags}
    site_host = urlparse(base_url).netloc.lower()
    written_posts = 0
    for item in posts:
        title = clean_text(str(item.get("title", {}).get("rendered") or "Untitled"))
        slug = slugify(str(item.get("slug") or title))
        raw_content = str(item.get("content", {}).get("rendered") or "")
        raw_excerpt = str(item.get("excerpt", {}).get("rendered") or "")
        category_ids = [int(value) for value in item.get("categories", [])]
        category_slug = next(
            (category_id_to_slug[value] for value in category_ids if category_id_to_slug.get(value) != "uncategorized"),
            category_id_to_slug.get(category_ids[0], "uncategorized") if category_ids else "uncategorized",
        )
        published_at = str(item.get("date_gmt") or item.get("date") or "")[:10]
        updated_at = str(item.get("modified_gmt") or item.get("modified") or published_at)[:10]
        summary = summary_from_html(raw_content, raw_excerpt)
        if not summary:
            summary = f"Archived media from the original site: {title}."
        post = {
            "title": title,
            "slug": slug,
            "category": category_slug,
            "summary": summary,
            "dek": "",
            "published_at": published_at,
            "updated_at": updated_at,
            "risk_score": 50,
            "tags": [tag_names[value] for value in item.get("tags", []) if tag_names.get(value)],
            "hero_image": "hero-panel",
            "sections": [],
            "body_html": sanitize_wordpress_html(raw_content, media_url_rewrites),
            "sources": extract_sources(raw_content, site_host),
            "sources_embedded": True,
            "legacy": {
                "source": "wordpress",
                "wp_id": int(item["id"]),
                "original_url": str(item.get("link") or f"{base_url}/{slug}/"),
            },
        }
        write_json(posts_dir / f"{published_at}-{slug}.json", post)
        written_posts += 1

    manifest = {
        "source": base_url,
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "counts": {
            "posts": len(posts),
            "pages": len(pages),
            "categories": len(categories),
            "tags": len(tags),
            "media": len(media),
            "media_downloaded": downloaded_media,
            "media_errors": len(media_errors),
        },
        "pages": [
            {
                "id": int(item["id"]),
                "slug": str(item.get("slug") or ""),
                "title": clean_text(str(item.get("title", {}).get("rendered") or "")),
                "url": str(item.get("link") or ""),
            }
            for item in pages
        ],
        "media_errors": media_errors,
    }
    write_json(content_dir / "wordpress-import.json", manifest)
    return {"posts": written_posts, "media": downloaded_media, "media_errors": len(media_errors)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the public WordPress archive into this static-site project.")
    parser.add_argument("--source", help="Override the configured WordPress base URL")
    args = parser.parse_args()
    result = import_wordpress(args.source)
    print(
        f"Imported {result['posts']} post(s) and {result['media']} media file(s) "
        f"with {result['media_errors']} media error(s)."
    )


if __name__ == "__main__":
    main()
