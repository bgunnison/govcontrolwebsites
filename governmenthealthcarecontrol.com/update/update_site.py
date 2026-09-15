from __future__ import annotations

import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.append(str(ROOT.parent))

from redaction import redact_text

from site_config import GENERATE_SETTINGS, SITE_SETTINGS
from update.prompt_lib import expand_topic_prompt, load_prompt_config, mark_topic_used, today_prompt_date
from site_lib import load_categories, load_posts, slugify, unique_slug, validate_post_dates, write_json

_LAST_OPENAI_REQUEST_AT = 0.0
_OPENAI_NOT_BEFORE = 0.0
_OPENAI_ESTIMATED_COST_USD = 0.0

MODEL_PRICING_USD_PER_MILLION = {
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "cached_input": 0.02, "output": 1.25},
}
WEB_SEARCH_COST_USD_PER_CALL = 0.01


class OpenAIRateLimitError(RuntimeError):
    """A temporary API throttle that should be resumed in a later run."""


class OpenAIQuotaError(RuntimeError):
    """A quota or billing error that retries cannot fix."""


class OpenAICostLimitError(RuntimeError):
    """The configured estimated API-spend limit has been reached."""


def append_log(path: Path, event: str, **fields: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "event": event,
    }
    record.update(fields)
    line = redact_text(json.dumps(record, ensure_ascii=False), (str(GENERATE_SETTINGS.get("api_key") or ""),))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


def estimate_response_cost(data: dict[str, Any], model: str) -> dict[str, int | float] | None:
    prices = MODEL_PRICING_USD_PER_MILLION.get(model)
    usage = data.get("usage")
    if prices is None or not isinstance(usage, dict):
        return None

    input_tokens = max(0, int(usage.get("input_tokens") or 0))
    output_tokens = max(0, int(usage.get("output_tokens") or 0))
    input_details = usage.get("input_tokens_details")
    cached_tokens = 0
    if isinstance(input_details, dict):
        cached_tokens = max(0, min(input_tokens, int(input_details.get("cached_tokens") or 0)))
    uncached_tokens = input_tokens - cached_tokens

    web_search_calls = sum(
        1
        for item in data.get("output", [])
        if isinstance(item, dict) and item.get("type") == "web_search_call"
    )
    token_cost = (
        uncached_tokens * prices["input"]
        + cached_tokens * prices["cached_input"]
        + output_tokens * prices["output"]
    ) / 1_000_000
    estimated_cost = token_cost + web_search_calls * WEB_SEARCH_COST_USD_PER_CALL
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "web_search_calls": web_search_calls,
        "estimated_cost_usd": estimated_cost,
    }


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


def current_update_cost(cfg: dict[str, Any]) -> float:
    ledger_value = str(cfg.get("cost_ledger_path") or "").strip()
    if ledger_value:
        return cost_ledger_total(Path(ledger_value))
    return _OPENAI_ESTIMATED_COST_USD


def enforce_cost_limit(cfg: dict[str, Any]) -> None:
    limit = max(0.0, float(cfg.get("max_estimated_cost_usd") or 0))
    if limit and current_update_cost(cfg) >= limit:
        raise OpenAICostLimitError(
            f"Estimated OpenAI API cost reached the configured ${limit:.2f} update limit."
        )


def record_response_cost(data: dict[str, Any], model: str, schema_name: str, cfg: dict[str, Any]) -> None:
    global _OPENAI_ESTIMATED_COST_USD
    cost = estimate_response_cost(data, model)
    if cost is None:
        append_log(
            Path(cfg["log_path"]),
            "api_usage_unavailable",
            model=model,
            schema=schema_name,
        )
        return

    _OPENAI_ESTIMATED_COST_USD += float(cost["estimated_cost_usd"])
    fields = {
        "site": str(SITE_SETTINGS["key"]),
        "model": model,
        "schema": schema_name,
        **cost,
    }
    append_log(Path(cfg["log_path"]), "api_usage", **fields)

    ledger_value = str(cfg.get("cost_ledger_path") or "").strip()
    if ledger_value:
        ledger_path = Path(ledger_value)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            **fields,
        }
        with open(ledger_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def http_ok(url: str, timeout: float = 10.0) -> bool:
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        return 200 <= response.status_code < 400
    except Exception:
        return False


def fetch_title_desc(url: str, timeout: float = 12.0) -> tuple[str, str]:
    try:
        response = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code >= 400:
            return "", ""
        html = response.text
        title = ""
        description = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            title = " ".join(title_match.group(1).split())
        desc_match = re.search(
            r"<meta[^>]+name=['\"]description['\"][^>]+content=['\"](.*?)['\"]",
            html,
            re.IGNORECASE,
        )
        if desc_match:
            description = " ".join(desc_match.group(1).split())
        return title, description
    except Exception:
        return "", ""


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query_pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_")]
    clean_query = urlencode(query_pairs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, clean_query, ""))


def extract_output_text(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                chunks.append(str(content["text"]).strip())
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def normalize_source_date(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Accept ISO timestamps, but never mine a date out of a title/event description.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def is_strictly_after(source_date: str, cutoff_date: str, *, through_date: str | None = None) -> bool:
    """A source must be newer than the cutoff, but never later than today."""
    source_iso = normalize_source_date(source_date)
    cutoff_iso = normalize_source_date(cutoff_date)
    through_iso = normalize_source_date(through_date or datetime.now().strftime("%Y-%m-%d"))
    if not source_iso or not cutoff_iso or not through_iso:
        return False
    return cutoff_iso < source_iso <= through_iso


def build_source_curation_request(topic_name: str, last_date: str, expanded_prompt: str, item_limit: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return f"""
Topic: {topic_name}
Last covered date: {last_date}
Today's date (inclusive upper bound): {today}

Research brief:
{expanded_prompt}

Use web search to find distinct candidate source items published strictly after {last_date} and on or before {today}. Each source should represent one separate story that could become its own website post.

Return a JSON object with this exact shape:
{{
  "sources": [
    {{
      "title": "exact source headline",
      "url": "https://example.com",
      "description": "short description",
      "published_at": "YYYY-MM-DD or empty string"
    }}
  ]
}}

Constraints:
- Return up to {item_limit} distinct sources.
- Use the topic prompt as the main relevance rule.
- Prefer primary sources, official announcements, research writeups, public videos, court documents, legislative material, and major reporting when relevant.
- For technical or culture topics, prefer the best direct source material rather than forcing a policy angle.
- Every title must be the exact source title.
- Every URL must be a direct public HTTP or HTTPS page.
- published_at must be the source page's actual publication date in YYYY-MM-DD, verified on the source page or its publication metadata; otherwise use an empty string.
- Never use an event/meeting date, registration deadline, law's effective date, or a date in the title/URL as a publication date.
- Never return a publication date after {today}. Upcoming events are eligible only when their announcement has a verified publication date in the allowed range.
- Do not guess a publication date or substitute today's date when it is unknown.
- Do not invent sources or URLs.
- Do not collapse many findings into one digest entry.
- If there are no solid new sources, return {{"sources": []}}.
""".strip()


def build_topic_request(topic_name: str, last_date: str, existing_titles: list[str], curated_sources: list[dict[str, str]]) -> str:
    existing_block = "\n".join(f"- {title}" for title in existing_titles[:20]) or "- None yet"
    source_lines = []
    for source in curated_sources:
        source_lines.append(
            f"- {source['title']} | {source.get('published_at', '')} | {source.get('description', '')} | {source['url']}"
        )
    sources_block = "\n".join(source_lines) or "- None"
    return f"""
Topic: {topic_name}
Last covered date: {last_date}

Already published titles in this topic:
{existing_block}

Approved source list:
{sources_block}

Use only the approved source list above. Each distinct source should become one separate website post whenever it is materially different from the already published titles.

Return a JSON object with this exact shape:
{{
  "items": [
    {{
      "title": "exact source title",
      "summary": "2 to 5 sentence summary paragraph",
      "risk_score": 1-100 integer,
      "tags": ["3 to 5 short tags"],
      "sources": [
        {{
          "title": "string",
          "url": "https://example.com",
          "description": "short description",
          "published_at": "YYYY-MM-DD or empty string"
        }}
      ]
    }}
  ]
}}

Constraints:
- Use only the approved source list. Do not invent or search for anything else.
- Copy each approved source's title, URL, and published_at unchanged; event dates belong in the summary, not publication metadata.
- Every item title must exactly match one source title from the approved list.
- Every item must be materially different from already published titles.
- Every item must include 1 to 4 approved source URLs that directly support it.
- Prefer one item per approved source when the source is distinct and worthwhile.
- Keep the tone factual, concise, and source-grounded.
- Write a plain summary paragraph only. Do not use subheaders or section labels.
- If there are no solid items, return {{"items": []}}.
""".strip()


def parse_duration_seconds(value: str | None) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    matches = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text)
    if matches and "".join(number + unit for number, unit in matches) == text.replace(" ", ""):
        multipliers = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
        return sum(float(number) * multipliers[unit] for number, unit in matches)
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def openai_error_details(response: requests.Response) -> tuple[str, str, str, str]:
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        error = {}
    error_type = str(error.get("type") or "").strip()
    error_code = str(error.get("code") or "").strip()
    message = str(error.get("message") or response.reason or "OpenAI request failed").strip()
    request_id = str(response.headers.get("x-request-id") or "").strip()
    return error_type, error_code, message, request_id


def retry_delay(response: requests.Response | None, attempt: int, cfg: dict[str, Any]) -> float:
    initial = max(0.1, float(cfg.get("initial_retry_delay_seconds", 5)))
    maximum = max(initial, float(cfg.get("max_retry_delay_seconds", 120)))
    jitter = max(0.0, float(cfg.get("retry_jitter_seconds", 1.5)))
    backoff = min(maximum, initial * (2 ** max(0, attempt - 1)))
    server_delay = parse_duration_seconds(response.headers.get("Retry-After")) if response is not None else None
    return max(backoff, server_delay or 0.0) + random.uniform(0.0, jitter)


def wait_for_request_slot(cfg: dict[str, Any]) -> None:
    global _LAST_OPENAI_REQUEST_AT
    minimum_interval = max(0.0, float(cfg.get("request_interval_seconds", 3)))
    now = time.monotonic()
    interval_deadline = _LAST_OPENAI_REQUEST_AT + minimum_interval if _LAST_OPENAI_REQUEST_AT else now
    deadline = max(interval_deadline, _OPENAI_NOT_BEFORE)
    if deadline > now:
        time.sleep(deadline - now)
    _LAST_OPENAI_REQUEST_AT = time.monotonic()


def rate_limit_reset_delay(response: requests.Response) -> float:
    delays: list[float] = []
    for remaining_header, reset_header in (
        ("x-ratelimit-remaining-requests", "x-ratelimit-reset-requests"),
        ("x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens"),
        ("x-ratelimit-remaining-project-tokens", "x-ratelimit-reset-project-tokens"),
    ):
        try:
            remaining = float(response.headers.get(remaining_header, "1"))
        except ValueError:
            continue
        if remaining <= 0:
            delay = parse_duration_seconds(response.headers.get(reset_header))
            if delay is not None:
                delays.append(delay)
    return max(delays, default=0.0)


def call_openai_json(
    api_key: str,
    model: str,
    instructions: str,
    request_text: str,
    schema_name: str,
    schema: dict[str, Any],
    *,
    tools: list[dict[str, Any]] | None = None,
    max_output_tokens: int = 6000,
    retry_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = retry_config or {}
    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": request_text,
        "store": False,
        "max_output_tokens": max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    if tools:
        payload["tools"] = tools

    max_attempts = max(1, int(cfg.get("max_api_attempts", 6)))
    max_elapsed = max(1.0, float(cfg.get("max_retry_elapsed_seconds", 600)))
    started = time.monotonic()
    last_error: Exception | None = None
    parse_failures = 0
    for attempt in range(1, max_attempts + 1):
        enforce_cost_limit(cfg)
        wait_for_request_slot(cfg)
        response: requests.Response | None = None
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=180,
            )
        except requests.RequestException as exc:
            last_error = RuntimeError(redact_text(f"OpenAI network error: {exc}", (api_key,)))
            if attempt >= max_attempts:
                raise last_error from exc
            delay = retry_delay(None, attempt, cfg)
            if time.monotonic() - started + delay > max_elapsed:
                raise OpenAIRateLimitError("OpenAI request deferred after repeated network errors.") from exc
            append_log(Path(cfg["log_path"]), "api_retry", attempt=attempt, status="network_error", wait_seconds=round(delay, 2))
            time.sleep(delay)
            continue

        if response.status_code >= 400:
            error_type, error_code, message, request_id = openai_error_details(response)
            message = redact_text(message, (api_key,))
            suffix = f" (request {request_id})" if request_id else ""
            if response.status_code == 429 and (
                error_type in {"insufficient_quota", "billing_error"}
                or error_code in {"insufficient_quota", "billing_hard_limit_reached", "usage_limit_reached"}
            ):
                raise OpenAIQuotaError(f"OpenAI quota or billing limit: {message}{suffix}")

            retryable = response.status_code == 429 or response.status_code in {500, 502, 503, 504}
            if retryable and attempt < max_attempts:
                delay = retry_delay(response, attempt, cfg)
                if time.monotonic() - started + delay > max_elapsed:
                    raise OpenAIRateLimitError(
                        f"OpenAI temporarily limited this update; retry later. Last status: {response.status_code}{suffix}"
                    )
                append_log(
                    Path(cfg["log_path"]),
                    "api_retry",
                    attempt=attempt,
                    status=response.status_code,
                    error_type=error_type,
                    error_code=error_code,
                    request_id=request_id,
                    wait_seconds=round(delay, 2),
                )
                time.sleep(delay)
                continue

            if response.status_code == 429:
                raise OpenAIRateLimitError(
                    f"OpenAI temporarily limited this update after {attempt} attempts; retry later{suffix}."
                )
            raise RuntimeError(
                f"OpenAI request failed with status {response.status_code}"
                f" ({error_code or error_type or 'unknown'}): {message}{suffix}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            last_error = RuntimeError("OpenAI returned a non-JSON response.")
            parse_failures += 1
            if parse_failures >= 2 or attempt >= max_attempts:
                raise last_error from exc
            continue
        record_response_cost(data, model, schema_name, cfg)
        text = extract_output_text(data)
        if not text:
            last_error = RuntimeError(
                f"OpenAI batch response did not contain JSON text (status={data.get('status', 'unknown')})"
            )
            parse_failures += 1
            if parse_failures >= 2:
                raise last_error
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            last_error = RuntimeError(f"OpenAI returned malformed JSON for {schema_name}: {exc}")
            parse_failures += 1
            if parse_failures >= 2:
                raise last_error from exc
            continue

        reset_delay = rate_limit_reset_delay(response)
        if reset_delay > 0:
            global _OPENAI_NOT_BEFORE
            delay = reset_delay + random.uniform(0.0, max(0.0, float(cfg.get("retry_jitter_seconds", 1.5))))
            _OPENAI_NOT_BEFORE = max(_OPENAI_NOT_BEFORE, time.monotonic() + delay)
            append_log(Path(cfg["log_path"]), "api_rate_window_scheduled", wait_seconds=round(delay, 2))
        return parsed

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"OpenAI request for {schema_name} failed without a parseable response.")


def call_openai_curated_sources(
    api_key: str,
    model: str,
    request_text: str,
    retry_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "description": {"type": "string"},
                        "published_at": {"type": "string"},
                    },
                    "required": ["title", "url", "description", "published_at"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["sources"],
        "additionalProperties": False,
    }
    return call_openai_json(
        api_key=api_key,
        model=model,
        instructions=(
            "You curate source lists for a public-interest research website. "
            "Use web search, return JSON only, and never invent sources or URLs."
        ),
        request_text=request_text,
        schema_name="topic_source_curation",
        schema=schema,
        tools=[{"type": "web_search", "search_context_size": "medium"}],
        max_output_tokens=5000,
        retry_config=retry_config,
    )


def call_openai_topic_batch(
    api_key: str,
    model: str,
    request_text: str,
    retry_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "risk_score": {"type": "integer"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "sources": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 4,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "url": {"type": "string"},
                                    "description": {"type": "string"},
                                    "published_at": {"type": "string"},
                                },
                                "required": ["title", "url", "description", "published_at"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["title", "summary", "risk_score", "tags", "sources"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    return call_openai_json(
        api_key=api_key,
        model=model,
        instructions=(
            "You write concise, source-grounded topic summaries for a public-interest website. "
            "Use only the approved source list in the prompt. "
            "Return JSON only and never invent sources or URLs."
        ),
        request_text=request_text,
        schema_name="topic_post_batch",
        schema=schema,
        max_output_tokens=6000,
        retry_config=retry_config,
    )


def validate_item_sources(
    raw_sources: Any,
    source_target: int,
    allowed_sources: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    curated: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(raw_sources, list):
        return curated

    for source in raw_sources:
        if not isinstance(source, dict):
            continue
        url = normalize_url(str(source.get("url") or "").strip())
        if not url or url in seen or not url.startswith(("http://", "https://")):
            continue
        if allowed_sources is not None and url not in allowed_sources:
            continue
        seen.add(url)
        # Summary output may select an approved URL, but must not rewrite its metadata.
        trusted = allowed_sources[url] if allowed_sources is not None else source
        title = str(trusted.get("title") or "").strip()
        description = str(trusted.get("description") or "").strip()
        published_at = normalize_source_date(str(trusted.get("published_at") or "").strip())
        if not published_at or published_at > datetime.now().strftime("%Y-%m-%d"):
            continue
        fetched_title = ""
        fetched_desc = ""
        if allowed_sources is None:
            if not http_ok(url):
                continue
            fetched_title, fetched_desc = fetch_title_desc(url)
        curated.append(
            {
                "title": title or fetched_title or url,
                "url": url,
                "description": description or fetched_desc,
                "published_at": published_at,
            }
        )
        if len(curated) >= source_target:
            break
    return curated


def existing_source_urls(posts: list[dict[str, Any]], category_slug: str) -> set[str]:
    urls: set[str] = set()
    for post in posts:
        if post.get("category") != category_slug:
            continue
        for source in post.get("sources", []):
            url = normalize_url(str(source.get("url") or "").strip())
            if url:
                urls.add(url)
    return urls


def stored_post_title(post: dict[str, Any]) -> str:
    sources = post.get("sources", [])
    if sources:
        source_title = str(sources[0].get("title") or "").strip()
        if source_title:
            return source_title
    return str(post.get("title") or "").strip()


def all_source_urls(posts: list[dict[str, Any]]) -> set[str]:
    urls: set[str] = set()
    for post in posts:
        for source in post.get("sources", []):
            url = normalize_url(str(source.get("url") or "").strip())
            if url:
                urls.add(url)
    return urls


def choose_prompt_topics(
    prompt_topics: list[dict[str, Any]],
    requested_topics: list[str] | None,
    limit: int,
    *,
    skip_through_date: str | None = None,
) -> list[dict[str, Any]]:
    selected = prompt_topics
    if requested_topics:
        requested = {topic.strip().lower() for topic in requested_topics}
        selected = [
            topic
            for topic in prompt_topics
            if str(topic.get("Topic") or "").strip().lower() in requested
            or slugify(str(topic.get("Topic") or "").strip()) in requested
        ]
    if skip_through_date:
        selected = [
            topic
            for topic in selected
            if not normalize_source_date(str(topic.get("LastDate") or ""))
            or normalize_source_date(str(topic.get("LastDate") or "")) < skip_through_date
        ]
    if limit > 0:
        return selected[:limit]
    return selected


def load_research_checkpoint(path: Path, topic_name: str, last_date: str) -> list[dict[str, str]] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("topic") != topic_name or data.get("last_date") != last_date:
        return None
    sources = data.get("sources")
    return sources if isinstance(sources, list) else None


def save_research_checkpoint(path: Path, topic_name: str, last_date: str, sources: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"topic": topic_name, "last_date": last_date, "sources": sources})


def persist_post(post: dict[str, Any]) -> Path:
    validate_post_dates([post])
    date_prefix = post["published_at"]
    filename = f"{date_prefix}-{slugify(post['slug'])}.json"
    target = Path(SITE_SETTINGS["posts_dir"]) / filename
    write_json(target, post)
    return target


def generate_posts_with_settings(cfg: dict[str, Any]) -> list[Path]:
    api_key = cfg.get("api_key")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured. Add it to the top-level private.py or the environment.")

    prompt_config = load_prompt_config()
    categories = load_categories()
    categories_by_slug = {category["slug"]: category for category in categories}
    posts = load_posts()
    existing_slugs = {post["slug"] for post in posts}
    all_title_lowers = {stored_post_title(post).lower() for post in posts if stored_post_title(post)}
    all_known_source_urls = all_source_urls(posts)
    selected_topics = choose_prompt_topics(
        prompt_config["topics"],
        cfg.get("topics"),
        int(cfg.get("max_topics", 0)),
        skip_through_date=(
            datetime.now().strftime("%Y-%m-%d")
            if cfg.get("skip_topics_updated_today", True) and not cfg.get("topics")
            else None
        ),
    )
    created: list[Path] = []
    failures: list[str] = []
    successful_topics = 0
    today_stamp = today_prompt_date()
    deferred_error: OpenAIRateLimitError | OpenAICostLimitError | None = None

    append_log(
        Path(cfg["log_path"]),
        "site_update_started",
        site=str(SITE_SETTINGS["key"]),
        subject=str(cfg.get("site_subject") or SITE_SETTINGS.get("research_subject") or "public policy"),
        topic_count=len(selected_topics),
    )

    for topic in selected_topics:
        topic_name = str(topic.get("Topic") or "").strip()
        category_slug = slugify(topic_name)
        category = categories_by_slug.get(
            category_slug,
            {
                "name": topic_name,
                "slug": category_slug,
                "description": "",
                "question": "",
                "hero_image": "hero-panel",
            },
        )
        category_posts = [post for post in posts if post.get("category") == category_slug]
        category_titles = [stored_post_title(post) for post in category_posts if stored_post_title(post)]
        category_title_lowers = {title.lower() for title in category_titles}
        category_source_urls = existing_source_urls(posts, category_slug)
        expanded_prompt = expand_topic_prompt(prompt_config["prompt_guide"], topic)
        last_date = str(topic.get("LastDate") or "")
        checkpoint_path = Path(cfg["log_path"]).parent / "pending" / f"{category_slug}.json"
        append_log(
            Path(cfg["log_path"]),
            "topic_research_start",
            category=category_slug,
            topic=topic_name,
            last_date=last_date,
            prompt=expanded_prompt,
        )

        curated_sources = load_research_checkpoint(checkpoint_path, topic_name, last_date)
        if curated_sources is not None:
            append_log(
                Path(cfg["log_path"]),
                "research_resumed",
                category=category_slug,
                count=len(curated_sources),
            )
        else:
            try:
                curated_batch = call_openai_curated_sources(
                    api_key=api_key,
                    model=str(cfg.get("research_model", cfg.get("model", "gpt-5.4"))),
                    request_text=build_source_curation_request(
                        topic_name=topic_name,
                        last_date=last_date,
                        expanded_prompt=expanded_prompt,
                        item_limit=int(cfg.get("items_per_topic", 16)),
                    ),
                    retry_config=cfg,
                )
            except OpenAIQuotaError as exc:
                append_log(Path(cfg["log_path"]), "quota_error", category=category_slug, reason=str(exc))
                raise
            except OpenAICostLimitError as exc:
                append_log(Path(cfg["log_path"]), "cost_limit_deferred", category=category_slug, reason=str(exc))
                failures.append(f"{category_slug}: cost limit")
                deferred_error = exc
                break
            except OpenAIRateLimitError as exc:
                append_log(Path(cfg["log_path"]), "rate_limit_deferred", category=category_slug, reason=str(exc))
                failures.append(f"{category_slug}: rate limited")
                deferred_error = exc
                break
            except Exception as exc:
                append_log(Path(cfg["log_path"]), "research_error", category=category_slug, reason=str(exc))
                failures.append(f"{category_slug}: research error")
                continue

            curated_sources = validate_item_sources(
                curated_batch.get("sources"),
                int(cfg.get("items_per_topic", 16)),
            )
        # Apply the same date window to fresh research AND resumed checkpoints.
        candidate_count = len(curated_sources)
        curated_sources = [
            {**source, "published_at": normalize_source_date(str(source.get("published_at") or ""))}
            for source in curated_sources
            if isinstance(source, dict)
            and is_strictly_after(str(source.get("published_at") or ""), last_date)
        ]
        if len(curated_sources) != candidate_count:
            append_log(
                Path(cfg["log_path"]), "sources_outside_date_window",
                category=category_slug, rejected=candidate_count - len(curated_sources),
                last_date=last_date, through_date=datetime.now().strftime("%Y-%m-%d"),
            )
        if curated_sources:
            save_research_checkpoint(checkpoint_path, topic_name, last_date, curated_sources)
        append_log(
            Path(cfg["log_path"]),
            "curated_sources",
            category=category_slug,
            count=len(curated_sources),
            sources=curated_sources,
        )

        if curated_sources:
            try:
                batch = call_openai_topic_batch(
                    api_key=api_key,
                    model=str(cfg.get("model", cfg.get("research_model", "gpt-5.4"))),
                    request_text=build_topic_request(
                        topic_name=topic_name,
                        last_date=str(topic.get("LastDate") or ""),
                        existing_titles=category_titles,
                        curated_sources=curated_sources,
                    ),
                    retry_config=cfg,
                )
            except OpenAIQuotaError as exc:
                append_log(Path(cfg["log_path"]), "quota_error", category=category_slug, reason=str(exc))
                raise
            except OpenAICostLimitError as exc:
                append_log(Path(cfg["log_path"]), "cost_limit_deferred", category=category_slug, reason=str(exc))
                failures.append(f"{category_slug}: cost limit")
                deferred_error = exc
                break
            except OpenAIRateLimitError as exc:
                append_log(Path(cfg["log_path"]), "rate_limit_deferred", category=category_slug, reason=str(exc))
                failures.append(f"{category_slug}: rate limited")
                deferred_error = exc
                break
            except Exception as exc:
                append_log(Path(cfg["log_path"]), "summary_error", category=category_slug, reason=str(exc))
                failures.append(f"{category_slug}: summary error")
                continue
            items = batch.get("items", [])
        else:
            items = []
        append_log(Path(cfg["log_path"]), "topic_response", category=category_slug, count=len(items))
        topic_created = 0
        allowed_sources = {source["url"]: source for source in curated_sources}

        for item in items:
            if not isinstance(item, dict):
                continue

            sources = validate_item_sources(
                item.get("sources"),
                int(cfg.get("source_target", 4)),
                allowed_sources=allowed_sources,
            )
            if len(sources) < 1:
                append_log(
                    Path(cfg["log_path"]),
                    "skipped_item",
                    category=category_slug,
                    reason="too_few_valid_sources",
                    title=str(item.get("title") or "").strip(),
                )
                continue
            title = sources[0]["title"] or str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not title:
                append_log(Path(cfg["log_path"]), "skipped_item", category=category_slug, reason="missing_title")
                continue
            if not summary:
                append_log(Path(cfg["log_path"]), "skipped_item", category=category_slug, reason="missing_summary", title=title)
                continue
            if title.lower() in all_title_lowers:
                append_log(Path(cfg["log_path"]), "skipped_item", category=category_slug, reason="duplicate_title_global", title=title)
                continue
            if title.lower() in category_title_lowers:
                append_log(Path(cfg["log_path"]), "skipped_item", category=category_slug, reason="duplicate_title", title=title)
                continue
            if all(source["url"] in all_known_source_urls for source in sources):
                append_log(Path(cfg["log_path"]), "skipped_item", category=category_slug, reason="duplicate_sources_global", title=title)
                continue
            if all(source["url"] in category_source_urls for source in sources):
                append_log(Path(cfg["log_path"]), "skipped_item", category=category_slug, reason="duplicate_sources", title=title)
                continue

            slug = unique_slug(title, existing_slugs)
            source_date = sources[0]["published_at"]
            post = {
                "title": title,
                "slug": slug,
                "category": category_slug,
                "summary": summary,
                "dek": "",
                "published_at": source_date,
                "published_at_basis": "source_publication_date",
                "created_at": datetime.now().strftime("%Y-%m-%d"),
                "updated_at": datetime.now().strftime("%Y-%m-%d"),
                "risk_score": int(item.get("risk_score") or 50),
                "tags": [str(tag).strip() for tag in item.get("tags", [])[:5] if str(tag).strip()],
                "hero_image": category.get("hero_image", "hero-panel"),
                "sections": [],
                "sources": [
                    {"title": source["title"], "url": source["url"], "published_at": source["published_at"]}
                    for source in sources
                ],
            }

            existing_slugs.add(slug)
            category_titles.append(title)
            category_title_lowers.add(title.lower())
            category_source_urls.update(source["url"] for source in sources)
            all_title_lowers.add(title.lower())
            all_known_source_urls.update(source["url"] for source in sources)
            posts.append(post)

            if cfg.get("dry_run"):
                append_log(Path(cfg["log_path"]), "dry_run_post", category=category_slug, post=post)
            else:
                post_path = persist_post(post)
                created.append(post_path)
                append_log(Path(cfg["log_path"]), "post_written", category=category_slug, post_path=str(post_path), slug=slug)
            topic_created += 1

        successful_topics += 1
        if not cfg.get("dry_run") and (topic_created > 0 or len(items) == 0):
            mark_topic_used(prompt_config, topic_name, today_stamp)
            append_log(Path(cfg["log_path"]), "topic_marked_used", category=category_slug, last_date=today_stamp)
        elif not cfg.get("dry_run"):
            append_log(Path(cfg["log_path"]), "topic_not_marked_used", category=category_slug, reason="no_valid_posts")
        checkpoint_path.unlink(missing_ok=True)
        append_log(Path(cfg["log_path"]), "topic_complete", category=category_slug, created=topic_created)

    if deferred_error is not None:
        if isinstance(deferred_error, OpenAICostLimitError):
            raise RuntimeError(
                "Update paused at the configured OpenAI cost limit. Completed topics were saved; "
                "raise OPENAI_MAX_UPDATE_COST_USD or run update.bat again to resume remaining topics."
            ) from deferred_error
        raise RuntimeError(
            "Update paused at a temporary OpenAI rate limit. Completed topics were saved; "
            "run update.bat again later to resume the remaining topics."
        ) from deferred_error
    if successful_topics == 0 and failures:
        raise RuntimeError("Research did not produce usable topics: " + "; ".join(failures))

    return created


if __name__ == "__main__":
    try:
        written = generate_posts_with_settings(GENERATE_SETTINGS)
        print(f"Generated {len(written)} new post(s).")
    except Exception as exc:
        print(f"Update incomplete: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        print(f"Estimated OpenAI API cost for this site run: ${_OPENAI_ESTIMATED_COST_USD:.4f}")
