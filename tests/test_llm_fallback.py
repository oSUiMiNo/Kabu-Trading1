"""
LLM クォータフォールバックのユニットテスト

agent_util.py のフォールバックロジックを検証する。
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))

import agent_util
from agent_util import (
    _is_fallback_target,
    _is_quota_error,
    _extract_reset_time,
    _extract_tools_from_agent,
    _notify_quota_fallback,
    _fallback_to_openai,
    _run_quota_exhausted,
    _run_fallback_failed,
    _run_notified,
    AgentResult,
    LLMFallbackError,
)


@pytest.fixture(autouse=True)
def reset_state():
    """各テスト前にモジュールレベルの状態をリセットする。"""
    _run_quota_exhausted.clear()
    _run_fallback_failed.clear()
    _run_notified.clear()
    yield
    _run_quota_exhausted.clear()
    _run_fallback_failed.clear()
    _run_notified.clear()


# ══════════════════════════════════════════════════════
# _is_fallback_target テスト
# ══════════════════════════════════════════════════════

class TestIsFallbackTarget:
    def test_usage_limit(self):
        err = Exception("You've hit your usage limit.")
        assert _is_fallback_target(err) is True

    def test_quota_exceeded(self):
        err = Exception("quota exceeded for this billing period")
        assert _is_fallback_target(err) is True

    def test_insufficient_quota(self):
        err = Exception("insufficient_quota")
        assert _is_fallback_target(err) is True

    def test_purchase_more_credits(self):
        err = Exception("purchase more credits or try again")
        assert _is_fallback_target(err) is True

    def test_rate_limit_not_fallback(self):
        err = Exception("Rate limit exceeded. Please retry after 60 seconds.")
        assert _is_fallback_target(err) is False

    def test_too_many_requests_not_fallback(self):
        err = Exception("Too many requests")
        assert _is_fallback_target(err) is False

    def test_generic_error_not_fallback(self):
        err = Exception("Connection timeout")
        assert _is_fallback_target(err) is False

    def test_auth_error_not_fallback(self):
        err = Exception("Invalid API key")
        assert _is_fallback_target(err) is False


class TestIsQuotaError:
    def test_includes_fallback_patterns(self):
        err = Exception("usage limit reached")
        assert _is_quota_error(err) is True

    def test_includes_rate_limit(self):
        err = Exception("rate limit exceeded")
        assert _is_quota_error(err) is True


# ══════════════════════════════════════════════════════
# _extract_reset_time テスト
# ══════════════════════════════════════════════════════

class TestExtractResetTime:
    def test_codex_format(self):
        err = Exception("try again at Apr 8th, 2026 8:00 AM.")
        assert _extract_reset_time(err) == "Apr 8th, 2026 8:00 AM"

    def test_no_reset_time(self):
        err = Exception("quota exceeded")
        assert _extract_reset_time(err) is None


# ══════════════════════════════════════════════════════
# _extract_tools_from_agent テスト
# ══════════════════════════════════════════════════════

class TestExtractToolsFromAgent:
    def test_websearch_in_frontmatter(self, tmp_path):
        md = tmp_path / "test-agent.md"
        md.write_text("---\ntools:\n  - WebSearch\n  - WebFetch\n---\nHello", encoding="utf-8")
        tools = _extract_tools_from_agent(str(md))
        assert tools == ["web_search"]

    def test_no_tools(self, tmp_path):
        md = tmp_path / "test-agent.md"
        md.write_text("---\nname: test\n---\nHello", encoding="utf-8")
        tools = _extract_tools_from_agent(str(md))
        assert tools == []

    def test_no_file(self):
        tools = _extract_tools_from_agent(None)
        assert tools == []

    def test_comment_websearch_not_detected(self, tmp_path):
        md = tmp_path / "test-agent.md"
        md.write_text("---\nname: test\n---\nUse WebSearch to find info", encoding="utf-8")
        tools = _extract_tools_from_agent(str(md))
        assert tools == []


# ══════════════════════════════════════════════════════
# _notify_quota_fallback テスト
# ══════════════════════════════════════════════════════

class TestNotifyQuotaFallback:
    @patch("agent_util.send_webhook", create=True)
    def test_notifies_once_per_window(self, mock_webhook):
        with patch.dict("sys.modules", {"discord_notifier": MagicMock(send_webhook=mock_webhook)}):
            _notify_quota_fallback("codex", "monitor-checker", "Apr 8th", "gpt-5.4")
            _notify_quota_fallback("codex", "analyst", "Apr 8th", "gpt-5.4")
            assert mock_webhook.call_count == 1

    @patch("agent_util.send_webhook", create=True)
    def test_different_provider_notifies_separately(self, mock_webhook):
        with patch.dict("sys.modules", {"discord_notifier": MagicMock(send_webhook=mock_webhook)}):
            _notify_quota_fallback("codex", "monitor-checker", "Apr 8th", "gpt-5.4")
            _notify_quota_fallback("glm", "analyst", "Apr 9th", "gpt-5.4")
            assert mock_webhook.call_count == 2


# ══════════════════════════════════════════════════════
# _fallback_to_openai テスト
# ══════════════════════════════════════════════════════

class TestFallbackToOpenai:
    @pytest.mark.anyio
    async def test_fallback_success(self):
        mock_result = AgentResult(text="fallback result", cost=0.01, tools_used=[])
        with patch("llm_client.call_openai", new_callable=AsyncMock, return_value=mock_result) as mock_call:
            with patch("agent_util._notify_quota_fallback"):
                with patch("agent_util._load_fallback_config", return_value={"model": "gpt-5.4", "reasoning_effort": "high"}):
                    err = Exception("usage limit reached. try again at Apr 8th.")
                    result = await _fallback_to_openai(
                        "test prompt", None, "test-agent", "codex", err,
                    )
                    assert result.text == "fallback result"
                    mock_call.assert_called_once()

    @pytest.mark.anyio
    async def test_transient_failure_does_not_block(self):
        """一時的エラー（タイムアウト等）は _run_fallback_failed に記録しない。"""
        with patch("llm_client.call_openai", new_callable=AsyncMock, side_effect=Exception("OpenAI down")):
            with patch("agent_util._notify_quota_fallback"):
                with patch("agent_util._load_fallback_config", return_value={"model": "gpt-5.4", "reasoning_effort": "high"}):
                    err = Exception("usage limit reached")
                    with pytest.raises(LLMFallbackError):
                        await _fallback_to_openai(
                            "test prompt", None, "test-agent", "codex", err,
                        )
                    assert "codex" not in _run_fallback_failed

    @pytest.mark.anyio
    async def test_auth_failure_blocks_future_attempts(self):
        """認証エラーは _run_fallback_failed に記録し、以降の試行をブロックする。"""
        call_count = 0
        async def counting_openai(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Exception("Invalid API key provided")

        with patch("llm_client.call_openai", new_callable=AsyncMock, side_effect=counting_openai):
            with patch("agent_util._notify_quota_fallback"):
                with patch("agent_util._load_fallback_config", return_value={"model": "gpt-5.4", "reasoning_effort": "high"}):
                    err = Exception("usage limit reached")
                    # 1回目: 認証エラーで失敗 → 記録される
                    with pytest.raises(LLMFallbackError):
                        await _fallback_to_openai(
                            "test", None, "test-agent", "codex", err,
                        )
                    assert call_count == 1
                    assert "codex" in _run_fallback_failed

                    # 2回目: OpenAI を叩かず即失敗
                    with pytest.raises(LLMFallbackError, match="既に失敗済み"):
                        await _fallback_to_openai(
                            "test", None, "test-agent", "codex", err,
                        )
                    assert call_count == 1

    @pytest.mark.anyio
    async def test_transient_failure_allows_retry(self):
        """一時的エラー後の2回目で OpenAI を再度試行する。"""
        call_count = 0
        async def counting_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Connection timeout")
            return AgentResult(text="recovered", cost=0.01, tools_used=[])

        with patch("llm_client.call_openai", new_callable=AsyncMock, side_effect=counting_then_succeed):
            with patch("agent_util._notify_quota_fallback"):
                with patch("agent_util._load_fallback_config", return_value={"model": "gpt-5.4", "reasoning_effort": "high"}):
                    err = Exception("usage limit reached")
                    # 1回目: 一時的エラーで失敗
                    with pytest.raises(LLMFallbackError):
                        await _fallback_to_openai(
                            "test", None, "test-agent", "codex", err,
                        )
                    assert call_count == 1

                    # 2回目: リトライして成功
                    result = await _fallback_to_openai(
                        "test", None, "test-agent", "codex", err,
                    )
                    assert call_count == 2
                    assert result.text == "recovered"


# ══════════════════════════════════════════════════════
# cooldown テスト
# ══════════════════════════════════════════════════════

class TestProviderCooldown:
    @pytest.mark.anyio
    async def test_cooldown_skips_original_provider(self):
        """cooldown 中は元 provider を試さず直接 fallback する。"""
        _run_quota_exhausted.add("codex")

        mock_result = AgentResult(text="fallback", cost=0.01, tools_used=[])
        with patch("agent_util._fallback_to_openai", new_callable=AsyncMock, return_value=mock_result) as mock_fb:
            with patch("agent_util._detect_provider", return_value=("codex", None)):
                from agent_util import call_agent
                result = await call_agent("test prompt", file_path=None)
                assert result.text == "fallback"
                mock_fb.assert_called_once()
