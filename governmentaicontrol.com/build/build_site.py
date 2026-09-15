from __future__ import annotations

import hashlib
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from site_config import SITE_SETTINGS
from site_lib import (
    category_lookup,
    category_post_counts,
    ensure_unique_post_slugs,
    estimate_reading_time,
    format_date,
    html_text,
    load_categories,
    load_posts,
    load_site_content,
    validate_post_dates,
)

SITE_COPY = load_site_content()


def asset_content_version() -> str:
    digest = hashlib.sha256()
    static_dir = Path(SITE_SETTINGS["static_dir"])
    asset_paths = [static_dir / "site.css", static_dir / "site.js"]
    asset_paths.extend(Path(path) for path in SITE_SETTINGS.get("media_files", {}).values())
    for path in sorted(set(asset_paths), key=lambda item: str(item).casefold()):
        digest.update(str(path.name).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


BUILD_ASSET_VERSION = asset_content_version()
MEDIA_OUTPUTS: dict[str, dict[str, object]] = {}
RASTER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_TAG_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def asset_url(path: str) -> str:
    return f"/assets/{path}".replace("\\", "/") + f"?v={BUILD_ASSET_VERSION}"


def post_url(slug: str) -> str:
    prefix = str(SITE_SETTINGS.get("post_url_prefix", "/posts")).rstrip("/")
    return f"{prefix}/{slug}/" if prefix else f"/{slug}/"


def posts_index_url() -> str:
    return "/posts/"


def category_url(slug: str) -> str:
    prefix = str(SITE_SETTINGS.get("category_url_prefix", "/categories")).rstrip("/")
    return f"{prefix}/{slug}/" if prefix else f"/{slug}/"


def relative_dir_from_url(url: str) -> str:
    return url.strip("/")


def page_title(title: str | None = None) -> str:
    site_name = SITE_COPY["branding"]["site_name"]
    return site_name if not title else f"{title} - {site_name}"


def prepare_post_seo_titles(categories: list[dict], posts: list[dict]) -> None:
    reserved = {
        str(SITE_SETTINGS.get("home_title") or SITE_COPY["branding"]["site_name"]).casefold(),
        str(SITE_COPY["pages"]["posts"]["title"]).casefold(),
        str(SITE_COPY["pages"]["categories"]["title"]).casefold(),
        str(SITE_COPY["pages"]["about"]["title"]).casefold(),
        *(str(category["name"]).casefold() for category in categories),
    }
    used = set(reserved)
    categories_by_slug = category_lookup(categories)
    used_descriptions = {
        str(SITE_COPY["branding"]["description"]).casefold(),
        str(SITE_COPY["pages"]["posts"]["intro"]).casefold(),
        str(SITE_COPY["pages"]["categories"]["intro"]).casefold(),
        str(SITE_COPY["pages"]["about"]["intro"]).casefold(),
        *(str(category["description"]).casefold() for category in categories),
    }
    for post in posts:
        base = display_post_title(post)
        candidate = base
        if candidate.casefold() in used:
            candidate = f"{base} ({format_date(post['published_at'])})"
        suffix = 2
        unique_candidate = candidate
        while unique_candidate.casefold() in used:
            unique_candidate = f"{candidate} - {suffix}"
            suffix += 1
        post["_seo_title"] = unique_candidate
        used.add(unique_candidate.casefold())
        summary = post_summary_paragraphs(post)
        description = summary[0] if summary else categories_by_slug[post["category"]]["description"]
        unique_description = description
        if unique_description.casefold() in used_descriptions:
            unique_description = f"{description.rstrip('.')} — published {format_date(post['published_at'])}."
        description_suffix = 2
        description_candidate = unique_description
        while description_candidate.casefold() in used_descriptions:
            description_candidate = f"{unique_description.rstrip('.')} — item {description_suffix}."
            description_suffix += 1
        post["_seo_description"] = description_candidate
        used_descriptions.add(description_candidate.casefold())


def append_image_attribute(tag: str, attribute: str) -> str:
    if tag.endswith("/>"):
        return f"{tag[:-2].rstrip()} {attribute} />"
    return f"{tag[:-1].rstrip()} {attribute}>"


def enhance_imported_images(value: str, title: str) -> str:
    fallback_alt = html_text(f"Image accompanying {title}")

    def enhance(match: re.Match[str]) -> str:
        tag = match.group(0)
        empty_alt = re.compile(r"\balt\s*=\s*([\"'])\s*\1", re.IGNORECASE)
        if empty_alt.search(tag):
            tag = empty_alt.sub(f'alt="{fallback_alt}"', tag, count=1)
        elif not re.search(r"\balt\s*=", tag, re.IGNORECASE):
            tag = append_image_attribute(tag, f'alt="{fallback_alt}"')
        if not re.search(r"\bloading\s*=", tag, re.IGNORECASE):
            tag = append_image_attribute(tag, 'loading="lazy"')
        if not re.search(r"\bdecoding\s*=", tag, re.IGNORECASE):
            tag = append_image_attribute(tag, 'decoding="async"')
        return tag

    return IMAGE_TAG_PATTERN.sub(enhance, value)


def rss_date(value: str) -> str:
    return datetime.strptime(value, "%Y-%m-%d").strftime("%a, %d %b %Y 00:00:00 +0000")


def path_is_active(href: str, current_path: str) -> bool:
    if href == "/":
        return current_path == "/"
    normalized = href.rstrip("/") + "/"
    return current_path == normalized or current_path.startswith(normalized)


def render_layout(*, title: str | None, description: str, canonical_path: str, body: str, body_class: str = "") -> str:
    site_name = SITE_COPY["branding"]["site_name"]
    site_url = SITE_SETTINGS["site_url"].rstrip("/")
    canonical = f"{site_url}{canonical_path}"
    meta_title = html_text(page_title(title))
    meta_description = html_text(description)
    logo_asset = MEDIA_OUTPUTS.get("logo", {})
    favicon_asset = MEDIA_OUTPUTS.get("favicon", {})
    logo_url = str(logo_asset.get("url") or "")
    favicon_url = str(favicon_asset.get("url") or "")
    topic_html = "\n".join(
        f'<a class="{"is-active" if path_is_active(category_url(topic["slug"]), canonical_path) else ""}" href="{category_url(topic["slug"])}">{html_text(topic["name"])}</a>'
        for topic in load_categories()
        if topic.get("nav", True)
    )
    footer_one = SITE_COPY["footer"]["line_one"]
    footer_two = SITE_COPY["footer"]["line_two"]
    footer_lines: list[str] = []
    for value in [footer_one, footer_two]:
        text = str(value or "").strip()
        if not text:
            continue
        footer_lines.extend(line.strip() for line in text.splitlines() if line.strip())
    footer_html = "".join(f"\n        <p>{html_text(line)}</p>" for line in footer_lines)
    logo_attributes = ""
    if logo_asset.get("srcset"):
        logo_attributes += f' srcset="{html_text(str(logo_asset["srcset"]))}" sizes="(max-width: 720px) 118px, 185px"'
    if logo_asset.get("width") and logo_asset.get("height"):
        logo_attributes += f' width="{int(logo_asset["width"])}" height="{int(logo_asset["height"])}"'
    logo_alt = str(SITE_SETTINGS.get("logo_alt") or f"{site_name} logo")
    logo_html = (
        f'<img class="wordmark-logo" src="{logo_url}"{logo_attributes} alt="{html_text(logo_alt)}" decoding="async" fetchpriority="high">'
        if logo_url
        else f'<span class="text-wordmark">{html_text(site_name)}</span>'
    )
    favicon_html = f'<link rel="icon" type="image/svg+xml" href="{favicon_url}">' if favicon_url else ""
    social_image_html = f'<meta property="og:image" content="{html_text(site_url + logo_url)}">' if logo_url else ""
    complete_body_class = f"site-{SITE_SETTINGS['key']} {body_class}".strip()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{meta_title}</title>
  <meta name="description" content="{meta_description}">
  <link rel="canonical" href="{html_text(canonical)}">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{meta_title}">
  <meta property="og:description" content="{meta_description}">
  <meta property="og:url" content="{html_text(canonical)}">
  <meta property="og:site_name" content="{html_text(site_name)}">
  {social_image_html}
  {favicon_html}
  <link rel="stylesheet" href="{asset_url('site.css')}">
</head>
<body class="{html_text(complete_body_class)}">
  <div class="page-backdrop"></div>
  <div id="page-wrapper">
    <header class="topbar">
      <div class="topbar-inner">
        <a class="brandmark" href="/">
          {logo_html}
        </a>
        <nav class="topic-nav" aria-label="Topics">
          {topic_html}
        </nav>
      </div>
    </header>
    <main class="site-main">
      {body}
    </main>
    <footer class="footer">
      <div class="footer-inner">
        {footer_html}
      </div>
    </footer>
  </div>
  <script src="{asset_url('site.js')}"></script>
</body>
</html>
"""


def render_metric(label: str, value: str) -> str:
    return f"""
    <div class="metric-card">
      <div class="metric-value">{html_text(value)}</div>
      <div class="metric-label">{html_text(label)}</div>
    </div>
    """


def archive_search_blob(post: dict, category_name: str) -> str:
    title = display_post_title(post)
    summary_text = " ".join(post_summary_paragraphs(post))
    return " ".join(
        [
            title,
            summary_text,
            category_name,
            " ".join(post.get("tags", [])),
        ]
    ).lower()


def display_post_title(post: dict) -> str:
    stored_title = str(post.get("title") or "").strip()
    if stored_title:
        return stored_title
    sources = post.get("sources", [])
    if sources:
        source_title = str(sources[0].get("title") or "").strip()
        if source_title:
            return source_title
    return str(post.get("title") or "").strip()


def post_summary_paragraphs(post: dict) -> list[str]:
    paragraphs: list[str] = []
    seen: set[str] = set()
    for value in [post.get("summary", ""), post.get("dek", "")]:
        text = str(value or "").strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            paragraphs.append(text)
    if paragraphs:
        return paragraphs
    for section in post.get("sections", []):
        for value in section.get("paragraphs", []):
            text = str(value or "").strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                paragraphs.append(text)
        if len(paragraphs) >= 2:
            break
    return paragraphs


def render_archive_entry(post: dict, categories_by_slug: dict[str, dict], *, show_category: bool = True) -> str:
    category = categories_by_slug[post["category"]]
    search_blob = archive_search_blob(post, category["name"])
    title = display_post_title(post)
    summary = post_summary_paragraphs(post)
    subtitle_html = ""
    body_html = ""
    if summary:
        subtitle_html = f'<p class="entry-subtitle"><strong>{html_text(summary[0])}</strong></p>'
        body_html = "\n".join(f"<p>{html_text(text)}</p>" for text in summary[1:])
    sources_html = "\n".join(
        f'<div class="source-item"><a href="{html_text(source["url"])}" target="_blank" rel="noreferrer">{html_text(source["title"])}</a></div>'
        for source in post.get("sources", [])
    )
    sources_section = ""
    if sources_html:
        sources_section = f"""
        <div class="entry-sources">
          <div class="entry-sources-label"><strong>Sources:</strong></div>
          <div class="source-list">
            {sources_html}
          </div>
        </div>
        """
    category_line = ""
    if show_category:
        category_line = (
            f'<div class="entry-byline-block"><span class="entry-byline-label">In:</span> '
            f'<a href="{category_url(category["slug"])}">{html_text(category["name"])}</a></div>'
        )
    return f"""
    <article class="archive-entry" data-post-card data-category="{html_text(category['slug'])}" data-search="{html_text(search_blob)}">
      <header class="archive-entry-header">
        <h2 class="entry-title"><a href="{post_url(post['slug'])}">{html_text(title)}</a></h2>
      </header>
      <div class="entry-byline">
        <div class="entry-byline-block"><span class="entry-byline-label">On:</span> <time datetime="{html_text(post['published_at'])}">{html_text(format_date(post['published_at']))}</time></div>
        {category_line}
      </div>
      <div class="entry-summary">
        {subtitle_html}
        {body_html}
      </div>
      {sources_section}
    </article>
    """


def render_post_card(post: dict, categories_by_slug: dict[str, dict], *, show_category: bool = True) -> str:
    category = categories_by_slug[post["category"]]
    title = display_post_title(post)
    summary = post_summary_paragraphs(post)
    search_blob = " ".join(
        [
            title,
            " ".join(summary),
            category["name"],
            " ".join(post.get("tags", [])),
        ]
    ).lower()
    category_html = (
        f'<a class="pill" href="{category_url(category["slug"])}">{html_text(category["name"])}</a>'
        if show_category
        else ""
    )
    return f"""
    <article class="post-card" data-post-card data-category="{html_text(category['slug'])}" data-search="{html_text(search_blob)}">
      <div class="post-card-top">
        {category_html}
        <span class="risk-chip">Risk {int(post.get("risk_score", 0))}</span>
      </div>
      <h3>{html_text(title)}</h3>
      <div class="post-meta">
        <span>{html_text(format_date(post['published_at']))}</span>
      </div>
      <p>{html_text(summary[0]) if summary else ""}</p>
    </article>
    """


def render_category_card(category: dict, count: int) -> str:
    count_text = f"{count} post" if count == 1 else f"{count} posts"
    return f"""
    <article class="category-card accent-{html_text(category['accent'])}">
      <div class="eyebrow">Category</div>
      <h3><a href="{category_url(category['slug'])}">{html_text(category['name'])}</a></h3>
      <p>{html_text(category['description'])}</p>
      <p class="category-question">{html_text(category['question'])}</p>
      <div class="card-footer">
        <span>{html_text(count_text)}</span>
        <a href="{category_url(category['slug'])}">Open archive</a>
      </div>
    </article>
    """


def render_latest_list_item(post: dict, categories_by_slug: dict[str, dict]) -> str:
    category = categories_by_slug[post["category"]]
    title = display_post_title(post)
    return f"""
    <li>
      <a class="wp-block-latest-posts__post-title" href="{post_url(post['slug'])}">{html_text(title)}</a>
      <time datetime="{html_text(post['published_at'])}" class="wp-block-latest-posts__post-date">{html_text(format_date(post['published_at']))}</time>
      <span class="post-topic-inline"> in <a href="{category_url(category['slug'])}">{html_text(category['name'])}</a></span>
    </li>
    """


def render_home(categories: list[dict], posts: list[dict], media_urls: dict[str, str]) -> str:
    categories_by_slug = category_lookup(categories)
    latest = posts[:18]
    latest_items = "\n".join(render_latest_list_item(post, categories_by_slug) for post in latest)
    description = SITE_COPY["branding"]["description"]
    site_name = SITE_COPY["branding"]["site_name"]
    latest_html = latest_items or '<li class="latest-empty">No posts yet.</li>'

    return render_layout(
        title=str(SITE_SETTINGS.get("home_title") or site_name),
        description=description,
        canonical_path="/",
        body=f"""
        <section class="home-hero section-shell">
          <h1 class="home-title-ribbon">{html_text(site_name)}</h1>
          <div class="home-welcome-panel">
            <p>{html_text(description)}</p>
          </div>
        </section>
        <section class="front-copy section-shell">
          <article class="front-entry">
            <div class="entry-content">
              <h2>Latest Posts</h2>
              <ul class="wp-block-latest-posts wp-block-latest-posts__list site-latest-posts">
                {latest_html}
              </ul>
            </div>
          </article>
        </section>
        """,
        body_class="page-home",
    )


def render_posts_index(categories: list[dict], posts: list[dict]) -> str:
    categories_by_slug = category_lookup(categories)
    page = SITE_COPY["pages"]["posts"]
    option_html = "\n".join(
        f'<option value="{html_text(cat["slug"])}">{html_text(cat["name"])}</option>' for cat in categories
    )
    entries = "\n".join(render_archive_entry(post, categories_by_slug) for post in posts)
    return render_layout(
        title=page["title"],
        description=page["intro"],
        canonical_path=posts_index_url(),
        body=f"""
        <section class="page-header">
          <div class="eyebrow">{html_text(page['eyebrow'])}</div>
          <h1>{html_text(page['title'])}</h1>
          <p>{html_text(page['intro'])}</p>
        </section>
        <section class="section-shell">
          <div class="filters">
            <input id="search-posts" data-search-input type="search" placeholder="Search title, summary, or tag">
            <select id="category-filter" data-category-filter>
              <option value="">All categories</option>
              {option_html}
            </select>
          </div>
          <div class="archive-list" data-post-grid>
            {entries}
          </div>
          <p class="empty-state" data-empty-state hidden>No posts match the current filter.</p>
        </section>
        """,
        body_class="page-archive",
    )


def render_categories_index(categories: list[dict], posts: list[dict]) -> str:
    counts = category_post_counts(posts)
    page = SITE_COPY["pages"]["categories"]
    entries = []
    for category in categories:
        count = counts.get(category["slug"], 0)
        count_text = f"{count} post" if count == 1 else f"{count} posts"
        entries.append(
            f"""
            <li class="category-list-entry">
              <h2><a href="{category_url(category['slug'])}">{html_text(category['name'])}</a></h2>
              <p>{html_text(category['description'])}</p>
              <p class="category-question">{html_text(category['question'])}</p>
              <p class="category-count">{html_text(count_text)}</p>
            </li>
            """
        )
    return render_layout(
        title=page["title"],
        description=page["intro"],
        canonical_path="/categories/",
        body=f"""
        <section class="page-header">
          <div class="eyebrow">{html_text(page['eyebrow'])}</div>
          <h1>{html_text(page['title'])}</h1>
          <p>{html_text(page['intro'])}</p>
        </section>
        <section class="section-shell">
          <ul class="category-archive-list">
            {''.join(entries)}
          </ul>
        </section>
        """,
        body_class="page-categories",
    )


def render_category_page(category: dict, posts: list[dict], categories_by_slug: dict[str, dict]) -> str:
    entries = "\n".join(render_archive_entry(post, categories_by_slug, show_category=False) for post in posts)
    if not entries:
        entries = '<p class="empty-state">No posts in this archive yet.</p>'
    return render_layout(
        title=category["name"],
        description=category["description"],
        canonical_path=category_url(category["slug"]),
        body=f"""
        <section class="page-header">
          <h1>{html_text(category['name'])}</h1>
        </section>
        <section class="section-shell">
          <div class="archive-list">
            {entries}
          </div>
        </section>
        """,
        body_class="page-category",
    )


def render_post_page(post: dict, categories_by_slug: dict[str, dict]) -> str:
    category = categories_by_slug[post["category"]]
    title = display_post_title(post)
    summary = post_summary_paragraphs(post)
    subtitle_html = ""
    body_html = ""
    imported_body = str(post.get("body_html") or "").strip()
    if imported_body:
        body_html = f'<div class="imported-content">{enhance_imported_images(imported_body, title)}</div>'
    elif summary:
        subtitle_html = f'<p class="entry-subtitle"><strong>{html_text(summary[0])}</strong></p>'
        body_html = "\n".join(f"<p>{html_text(text)}</p>" for text in summary[1:])
    sources_html = "\n".join(
        f'<div class="source-item"><a href="{html_text(source["url"])}" target="_blank" rel="noreferrer">{html_text(source["title"])}</a></div>'
        for source in post.get("sources", [])
    )
    sources_section = ""
    if sources_html and not post.get("sources_embedded"):
        sources_section = f"""
        <div class="entry-sources">
          <div class="entry-sources-label"><strong>Sources:</strong></div>
          <div class="source-list">
            {sources_html}
          </div>
        </div>
        """
    description = str(post.get("_seo_description") or (summary[0] if summary else category["description"]))
    return render_layout(
        title=str(post.get("_seo_title") or title),
        description=description,
        canonical_path=post_url(post["slug"]),
        body=f"""
        <article class="article-shell">
          <header class="article-header">
            <h1>{html_text(title)}</h1>
            <div class="entry-byline">
              <div class="entry-byline-block"><span class="entry-byline-label">On:</span> <time datetime="{html_text(post['published_at'])}">{html_text(format_date(post['published_at']))}</time></div>
              <div class="entry-byline-block"><span class="entry-byline-label">In:</span> <a href="{category_url(category['slug'])}">{html_text(category['name'])}</a></div>
            </div>
          </header>
          <div class="article-body">
            {subtitle_html}
            {body_html}
          </div>
          {sources_section}
        </article>
        """,
        body_class="page-post",
    )


def render_about_page() -> str:
    page = SITE_COPY["pages"]["about"]
    cards_html = "\n".join(
        (
            f'<article class="category-card accent-{html_text(card["accent"])}">'
            f'<div class="eyebrow">{html_text(card["eyebrow"])}</div>'
            f'<h3>{html_text(card["title"])}</h3>'
            f'<p>{html_text(card["body"])}</p>'
            "</article>"
        )
        for card in page["cards"]
    )
    return render_layout(
        title=page["title"],
        description=page["intro"],
        canonical_path="/about/",
        body=f"""
        <section class="page-header">
          <div class="eyebrow">{html_text(page['eyebrow'])}</div>
          <h1>{html_text(page['title'])}</h1>
          <p>{html_text(page['intro'])}</p>
        </section>
        <section class="section-shell about-grid">
          {cards_html}
        </section>
        """,
        body_class="page-about",
    )


def build_rss(posts: list[dict]) -> str:
    site_name = SITE_COPY["branding"]["site_name"]
    site_url = SITE_SETTINGS["site_url"].rstrip("/")
    items = []
    for post in posts[:25]:
        title = display_post_title(post)
        summary = post_summary_paragraphs(post)
        description = html_text(summary[0] if summary else "")
        items.append(
            f"""
    <item>
      <title>{html_text(title)}</title>
      <link>{site_url}{post_url(post['slug'])}</link>
      <guid>{site_url}{post_url(post['slug'])}</guid>
      <pubDate>{rss_date(post['published_at'])}</pubDate>
      <description>{description}</description>
    </item>""".rstrip()
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{html_text(site_name)}</title>
    <link>{site_url}</link>
    <description>{html_text(SITE_COPY['branding']['description'])}</description>
{''.join(items)}
  </channel>
</rss>
"""


def build_sitemap(posts: list[dict], categories: list[dict]) -> str:
    site_url = SITE_SETTINGS["site_url"].rstrip("/")
    urls = ["/", posts_index_url(), "/categories/", "/about/"]
    urls.extend(post_url(post["slug"]) for post in posts)
    urls.extend(category_url(category["slug"]) for category in categories)
    entries = "\n".join(f"<url><loc>{site_url}{path}</loc></url>" for path in urls)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{entries}
</urlset>
"""


def build_responsive_raster(source: Path, key: str, media_dir: Path) -> dict[str, object]:
    configured_widths = SITE_SETTINGS.get("media_widths", {})
    default_widths = [192, 384] if key == "logo" else [640, 960]
    requested = configured_widths.get(key, default_widths) if isinstance(configured_widths, dict) else default_widths
    with Image.open(source) as opened:
        prepared = ImageOps.exif_transpose(opened)
        has_alpha = "A" in prepared.getbands() or prepared.info.get("transparency") is not None
        prepared = prepared.convert("RGBA" if has_alpha else "RGB")
        widths = sorted({min(int(width), prepared.width) for width in requested if int(width) > 0})
        if not widths:
            widths = [prepared.width]
        generated: list[tuple[str, int, int, int]] = []
        for width in widths:
            height = max(1, round(prepared.height * width / prepared.width))
            resized = prepared if (width, height) == prepared.size else prepared.resize((width, height), Image.Resampling.LANCZOS)
            target = media_dir / f"{key}-{width}.webp"
            resized.save(target, "WEBP", quality=90, method=6)
            generated.append((asset_url(f"media/{target.name}"), width, height, target.stat().st_size))
        primary_url, primary_width, primary_height, _ = generated[-1]
        return {
            "url": primary_url,
            "srcset": ", ".join(f"{url} {width}w" for url, width, _, _ in generated),
            "width": primary_width,
            "height": primary_height,
        }


def copy_static_assets(dist_dir: Path) -> dict[str, str]:
    global MEDIA_OUTPUTS
    MEDIA_OUTPUTS = {}
    static_dir = Path(SITE_SETTINGS["static_dir"])
    shutil.copy2(static_dir / "site.css", dist_dir / "assets" / "site.css")
    shutil.copy2(static_dir / "site.js", dist_dir / "assets" / "site.js")

    media_urls: dict[str, str] = {}
    media_dir = dist_dir / "assets" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    for key, source_path in SITE_SETTINGS["media_files"].items():
        source = Path(source_path)
        if source.suffix.lower() in RASTER_EXTENSIONS:
            output = build_responsive_raster(source, key, media_dir)
        else:
            target = media_dir / f"{key}{source.suffix.lower()}"
            shutil.copy2(source, target)
            output = {"url": asset_url(f"media/{target.name}")}
        MEDIA_OUTPUTS[key] = output
        media_urls[key] = str(output["url"])
    imported_media_dir = Path(SITE_SETTINGS["imported_media_dir"])
    if imported_media_dir.exists():
        shutil.copytree(imported_media_dir, media_dir / "imported", dirs_exist_ok=True)
    return media_urls


def build_htaccess() -> str:
    site_url = str(SITE_SETTINGS["site_url"]).rstrip("/")
    canonical_host = urlparse(site_url).netloc
    escaped_host = re.escape(canonical_host)
    return f"""# Generated canonical-host and HTTPS redirect rules.
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteCond %{{HTTPS}} !=on [OR]
RewriteCond %{{HTTP_HOST}} !^{escaped_host}$ [NC]
RewriteRule ^ {site_url}%{{REQUEST_URI}} [R=301,L,NE]
</IfModule>
"""


def write_page(dist_dir: Path, relative_dir: str, html: str) -> None:
    target_dir = dist_dir / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    with open(target_dir / "index.html", "w", encoding="utf-8") as handle:
        handle.write(html)


def build_site() -> Path:
    categories = load_categories()
    posts = load_posts()
    ensure_unique_post_slugs(posts)
    validate_post_dates(posts)
    prepare_post_seo_titles(categories, posts)
    categories_by_slug = category_lookup(categories)

    dist_dir = Path(SITE_SETTINGS["dist_dir"])
    if dist_dir.exists():
        try:
            shutil.rmtree(dist_dir)
        except PermissionError as exc:
            raise RuntimeError("Could not clean dist/. Close any file handles or local preview processes using dist and try again.") from exc
    (dist_dir / "assets").mkdir(parents=True, exist_ok=True)
    media_urls = copy_static_assets(dist_dir)

    write_page(dist_dir, "", render_home(categories, posts, media_urls))
    write_page(dist_dir, "posts", render_posts_index(categories, posts))
    write_page(dist_dir, "categories", render_categories_index(categories, posts))
    write_page(dist_dir, "about", render_about_page())

    for category in categories:
        category_posts = [post for post in posts if post["category"] == category["slug"]]
        rendered_category = render_category_page(category, category_posts, categories_by_slug)
        write_page(
            dist_dir,
            relative_dir_from_url(category_url(category["slug"])),
            rendered_category,
        )
        for alias_prefix in SITE_SETTINGS.get("category_alias_prefixes", []):
            alias_url = f"{str(alias_prefix).rstrip('/')}/{category['slug']}/"
            write_page(dist_dir, relative_dir_from_url(alias_url), rendered_category)
    for post in posts:
        write_page(
            dist_dir,
            relative_dir_from_url(post_url(post["slug"])),
            render_post_page(post, categories_by_slug),
        )

    rss = build_rss(posts)
    with open(dist_dir / "feed.xml", "w", encoding="utf-8") as handle:
        handle.write(rss)
    write_page(dist_dir, "feed", rss)
    with open(dist_dir / "sitemap.xml", "w", encoding="utf-8") as handle:
        handle.write(build_sitemap(posts, categories))
    if SITE_SETTINGS["key"] == "gun":
        with open(dist_dir / "wp-sitemap.xml", "w", encoding="utf-8") as handle:
            handle.write(build_sitemap(posts, categories))
        with open(dist_dir / "sitemap_index.xml", "w", encoding="utf-8") as handle:
            handle.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                f'  <sitemap><loc>{SITE_SETTINGS["site_url"]}/sitemap.xml</loc></sitemap>\n'
                '</sitemapindex>\n'
            )
    with open(dist_dir / "robots.txt", "w", encoding="utf-8") as handle:
        handle.write(f"User-agent: *\nAllow: /\nSitemap: {SITE_SETTINGS['site_url']}/sitemap.xml\n")
    with open(dist_dir / ".htaccess", "w", encoding="utf-8") as handle:
        handle.write(build_htaccess())

    return dist_dir


if __name__ == "__main__":
    output = build_site()
    print(f"Built static site into {output}")
