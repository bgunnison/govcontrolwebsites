from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from update import update_site


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.reason = "test response"

    def json(self) -> dict:
        return self._payload


def verify_update_resilience() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        config = {
            "log_path": Path(temp_dir) / "test.jsonl",
            "request_interval_seconds": 0,
            "max_api_attempts": 3,
            "initial_retry_delay_seconds": 0.1,
            "max_retry_delay_seconds": 0.1,
            "max_retry_elapsed_seconds": 5,
            "retry_jitter_seconds": 0,
        }
        rate_limited = FakeResponse(
            429,
            {"error": {"type": "rate_limit_error", "code": "slow_down", "message": "slow down"}},
            {"Retry-After": "0", "x-request-id": "req_test"},
        )
        success = FakeResponse(200, {"output_text": '{"ok": true}'})
        update_site._LAST_OPENAI_REQUEST_AT = 0
        update_site._OPENAI_NOT_BEFORE = 0
        with patch.object(update_site.requests, "post", side_effect=[rate_limited, success]) as request_mock:
            with patch.object(update_site.time, "sleep"), patch.object(update_site, "append_log"):
                result = update_site.call_openai_json(
                    api_key="test-key",
                    model="test-model",
                    instructions="test",
                    request_text="test",
                    schema_name="test_schema",
                    schema={"type": "object"},
                    retry_config=config,
                )
        assert result == {"ok": True}
        assert request_mock.call_count == 2

        quota_limited = FakeResponse(
            429,
            {"error": {"type": "insufficient_quota", "code": "insufficient_quota", "message": "quota"}},
        )
        update_site._LAST_OPENAI_REQUEST_AT = 0
        update_site._OPENAI_NOT_BEFORE = 0
        with patch.object(update_site.requests, "post", return_value=quota_limited):
            try:
                update_site.call_openai_json(
                    api_key="test-key",
                    model="test-model",
                    instructions="test",
                    request_text="test",
                    schema_name="test_schema",
                    schema={"type": "object"},
                    retry_config=config,
                )
            except update_site.OpenAIQuotaError:
                pass
            else:
                raise AssertionError("Quota errors must fail immediately.")

        usage_payload = {
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 100},
                "output_tokens": 200,
            },
            "output": [{"type": "web_search_call", "action": {"type": "search"}}],
        }
        cost = update_site.estimate_response_cost(usage_payload, "gpt-5.4")
        assert cost is not None
        assert abs(float(cost["estimated_cost_usd"]) - 0.015275) < 0.0000001

        ledger_path = Path(temp_dir) / "cost.jsonl"
        cost_config = {**config, "cost_ledger_path": ledger_path, "max_estimated_cost_usd": 0.01}
        update_site._OPENAI_ESTIMATED_COST_USD = 0
        with patch.object(update_site, "append_log"):
            update_site.record_response_cost(usage_payload, "gpt-5.4", "test_schema", cost_config)
        assert abs(update_site.cost_ledger_total(ledger_path) - 0.015275) < 0.0000001
        try:
            update_site.enforce_cost_limit(cost_config)
        except update_site.OpenAICostLimitError:
            pass
        else:
            raise AssertionError("The configured cost limit must stop another API request.")

    topics = [
        {"Topic": "Done", "LastDate": "09/06/2026"},
        {"Topic": "Due", "LastDate": "09/05/2026"},
    ]
    selected = update_site.choose_prompt_topics(topics, None, 0, skip_through_date="2026-09-06")
    assert [topic["Topic"] for topic in selected] == ["Due"]
    assert update_site.parse_duration_seconds("1m30s") == 90


if __name__ == "__main__":
    verify_update_resilience()
    print("Update resilience checks passed.")
