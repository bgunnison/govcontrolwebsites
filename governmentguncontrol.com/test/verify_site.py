from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from site_config import SITE_SETTINGS
from site_lib import validate_post_dates

LINK_PATTERN = re.compile(r'(?:href|src)="([^"]+)"', re.IGNORECASE)
HREF_PATTERN = re.compile(r'href="([^"]+)"', re.IGNORECASE)
TITLE_PATTERN = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
DESCRIPTION_PATTERN = re.compile(r'<meta\s+name="description"\s+content="(.*?)">', re.IGNORECASE | re.DOTALL)
CANONICAL_PATTERN = re.compile(r'<link\s+rel="canonical"\s+href="([^"]+)">', re.IGNORECASE)
H1_PATTERN = re.compile(r"<h1\b", re.IGNORECASE)
IMAGE_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
JSON_LD_PATTERN = re.compile(
    r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def local_target(dist_dir: Path, url: str) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return None
    path = parsed.path
    if path == "/":
        return dist_dir / "index.html"
    candidate = dist_dir / path.lstrip("/")
    if path.endswith("/"):
        candidate = candidate / "index.html"
    return candidate


def route_for_html(dist_dir: Path, html_path: Path) -> str:
    relative = html_path.relative_to(dist_dir)
    if relative == Path("index.html"):
        return "/"
    return f"/{relative.parent.as_posix().strip('/')}/"


def duplicate_values(records: list[dict[str, str]], field: str) -> list[str]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for record in records:
        value = unescape(record[field]).strip()
        grouped[value.casefold()].append(record["route"])
    return [
        f"{records_for_value[0]} and {records_for_value[1]} ({field})"
        for records_for_value in grouped.values()
        if len(records_for_value) > 1
    ]


def verify_site() -> dict[str, int]:
    dist_dir = Path(SITE_SETTINGS["dist_dir"])
    posts_dir = Path(SITE_SETTINGS["posts_dir"])
    categories_path = Path(SITE_SETTINGS["content_dir"]) / "categories.json"
    if not dist_dir.exists():
        raise RuntimeError(f"Build output does not exist: {dist_dir}")

    errors: list[str] = []
    categories = json.loads(categories_path.read_text(encoding="utf-8"))
    category_slugs = {item["slug"] for item in categories}
    posts = [json.loads(path.read_text(encoding="utf-8")) for path in posts_dir.glob("*.json")]
    try:
        validate_post_dates(posts)
    except ValueError as exc:
        errors.append(str(exc))
    unknown_categories = sorted({post.get("category") for post in posts} - category_slugs)
    if unknown_categories:
        errors.append(f"Posts reference unknown categories: {', '.join(unknown_categories)}")

    site_url = str(SITE_SETTINGS["site_url"]).rstrip("/")
    site_host = urlparse(site_url).netloc
    if urlparse(site_url).scheme != "https":
        errors.append(f"Canonical site URL must use HTTPS: {site_url}")

    html_files = list(dist_dir.rglob("*.html"))
    page_records: list[dict[str, str]] = []
    broken: list[str] = []
    incoming_urls: set[str] = set()

    for html_path in html_files:
        content = html_path.read_text(encoding="utf-8")
        if not content.lstrip().lower().startswith("<!doctype html>"):
            continue

        relative = str(html_path.relative_to(dist_dir))
        route = route_for_html(dist_dir, html_path)
        title_match = TITLE_PATTERN.search(content)
        description_match = DESCRIPTION_PATTERN.search(content)
        canonical_match = CANONICAL_PATTERN.search(content)
        if not title_match:
            errors.append(f"{relative}: missing title")
        if not description_match:
            errors.append(f"{relative}: missing meta description")
        if not canonical_match:
            errors.append(f"{relative}: missing canonical URL")
        if not H1_PATTERN.search(content):
            errors.append(f"{relative}: missing H1")

        for image_number, tag in enumerate(IMAGE_PATTERN.findall(content), start=1):
            alt_match = re.search(r'\balt\s*=\s*"([^"]*)"', tag, re.IGNORECASE)
            if not alt_match or not unescape(alt_match.group(1)).strip():
                errors.append(f"{relative}: image {image_number} is missing meaningful alt text")
            if not re.search(r"\bwidth\s*=", tag, re.IGNORECASE) or not re.search(
                r"\bheight\s*=", tag, re.IGNORECASE
            ):
                errors.append(f"{relative}: image {image_number} is missing width or height")

        for json_ld_number, payload in enumerate(JSON_LD_PATTERN.findall(content), start=1):
            try:
                json.loads(payload.strip())
            except (TypeError, ValueError) as exc:
                errors.append(f"{relative}: malformed JSON-LD block {json_ld_number}: {exc}")

        for url in LINK_PATTERN.findall(content):
            target = local_target(dist_dir, unescape(url))
            if target is not None and not target.exists():
                broken.append(f"{relative} -> {url}")

        for href in HREF_PATTERN.findall(content):
            href = unescape(href)
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(f"{site_url}{route}", href)
            parsed = urlparse(absolute)
            if parsed.netloc == site_host:
                normalized_path = parsed.path or "/"
                incoming_urls.add(f"{site_url}{normalized_path}")

        if title_match and description_match and canonical_match:
            canonical = unescape(canonical_match.group(1)).strip()
            parsed_canonical = urlparse(canonical)
            if parsed_canonical.scheme != "https" or parsed_canonical.netloc != site_host:
                errors.append(f"{relative}: canonical uses the wrong origin: {canonical}")
            canonical_target = local_target(dist_dir, parsed_canonical.path)
            if canonical_target is None or not canonical_target.exists():
                errors.append(f"{relative}: canonical target does not exist locally: {canonical}")
            page_records.append(
                {
                    "route": route,
                    "title": title_match.group(1),
                    "description": description_match.group(1),
                    "canonical": canonical,
                    "self_canonical": str(canonical == f"{site_url}{route}"),
                }
            )

    if broken:
        errors.append(f"Broken internal links ({len(broken)}): {broken[0]}")

    canonical_records = [record for record in page_records if record["self_canonical"] == "True"]
    duplicate_titles = duplicate_values(canonical_records, "title")
    duplicate_descriptions = duplicate_values(canonical_records, "description")
    if duplicate_titles:
        errors.append(f"Duplicate titles ({len(duplicate_titles)}): {duplicate_titles[0]}")
    if duplicate_descriptions:
        errors.append(f"Duplicate meta descriptions ({len(duplicate_descriptions)}): {duplicate_descriptions[0]}")

    prefix = str(SITE_SETTINGS.get("post_url_prefix", "/posts")).rstrip("/")
    expected_post_urls = {
        f"{site_url}{prefix}/{post['slug']}/" if prefix else f"{site_url}/{post['slug']}/"
        for post in posts
    }
    orphaned_posts = sorted(expected_post_urls - incoming_urls)
    if orphaned_posts:
        errors.append(f"Orphaned article pages ({len(orphaned_posts)}): {orphaned_posts[0]}")

    try:
        ET.parse(dist_dir / "feed.xml")
    except (ET.ParseError, OSError) as exc:
        errors.append(f"Invalid RSS feed: {exc}")

    try:
        sitemap_tree = ET.parse(dist_dir / "sitemap.xml")
        sitemap_locations = [
            str(element.text or "").strip()
            for element in sitemap_tree.getroot().findall(".//{*}loc")
        ]
        if len(sitemap_locations) != len(set(sitemap_locations)):
            errors.append("Sitemap contains duplicate URLs")
        canonical_urls = {record["canonical"] for record in canonical_records}
        sitemap_urls = set(sitemap_locations)
        missing_from_sitemap = sorted(canonical_urls - sitemap_urls)
        extra_in_sitemap = sorted(sitemap_urls - canonical_urls)
        if missing_from_sitemap:
            errors.append(f"Canonical page missing from sitemap: {missing_from_sitemap[0]}")
        if extra_in_sitemap:
            errors.append(f"Sitemap URL has no self-canonical page: {extra_in_sitemap[0]}")
    except (ET.ParseError, OSError) as exc:
        errors.append(f"Invalid XML sitemap: {exc}")

    robots_path = dist_dir / "robots.txt"
    expected_sitemap_line = f"Sitemap: {site_url}/sitemap.xml"
    if not robots_path.is_file() or expected_sitemap_line not in robots_path.read_text(encoding="utf-8"):
        errors.append("robots.txt does not advertise the canonical sitemap")

    htaccess_path = dist_dir / ".htaccess"
    if not htaccess_path.is_file():
        errors.append("Canonical redirect file is missing")
    else:
        htaccess = htaccess_path.read_text(encoding="utf-8")
        if f"RewriteRule ^ {site_url}%{{REQUEST_URI}}" not in htaccess:
            errors.append("Canonical redirect target does not match site_url")
        if f"!^{re.escape(site_host)}$" not in htaccess:
            errors.append("Canonical redirect host condition does not match site_url")

    if errors:
        sample = "\n".join(f"- {item}" for item in errors[:30])
        raise RuntimeError(f"SEO/site verification failed ({len(errors)} issues):\n{sample}")

    return {
        "posts": len(posts),
        "categories": len(categories),
        "html_pages": len(html_files),
        "seo_pages": len(canonical_records),
        "assets": len([path for path in (dist_dir / "assets").rglob("*") if path.is_file()]),
    }


if __name__ == "__main__":
    result = verify_site()
    print(
        f"Verified {SITE_SETTINGS['site_name']}: {result['posts']} posts, "
        f"{result['categories']} categories, {result['html_pages']} HTML pages, "
        f"{result['assets']} assets."
    )
