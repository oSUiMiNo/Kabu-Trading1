"""
外部 LLM プロバイダー共有クライアント

call_agent()（Claude Agent SDK）と同等のインターフェースで
OpenAI / GLM を呼び出す。戻り値は AgentResult 互換。

OpenAI:
  - tools 未指定 → Chat Completions API（テキスト生成のみ）
  - tools 指定   → Responses API（web_search 等のツール使用可能）
GLM:
  AsyncOpenAI の base_url を Z.AI エンドポイントに向けて使用
  （公式推奨の OpenAI 互換モード）。

依存パッケージ:
    openai>=2.20   … call_openai / call_glm 両方で使用
    python-dotenv  … .env.local 読み込み
    pyyaml         … エージェントファイルのフロントマターパース

Usage:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
    from llm_client import call_openai, call_glm

    # テキスト生成のみ
    result = await call_openai("要約して")

    # ウェブ検索付き（Responses API）
    result = await call_openai("NVDAの最新株価は？", tools=["web_search"])

    # GLM
    result = await call_glm("NVDAを分析して", model="glm-4.7-flash")
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import yaml
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# USD per 1M tokens: (input, cached_input, output)
_OPENAI_PRICING: dict[str, tuple[float, float, float]] = {
    "gpt-5.2":      (1.75,  0.175, 14.00),
    "gpt-5.1":      (1.25,  0.125, 10.00),
    "gpt-5":        (1.25,  0.125, 10.00),
    "gpt-5-mini":   (0.25,  0.025,  2.00),
    "o3":           (2.00,  0.50,   8.00),
    "o4-mini":      (1.10,  0.275,  4.40),
    "o1":          (15.00,  7.50,  60.00),
    "gpt-4o":       (2.50,  1.25,  10.00),
    "gpt-4o-mini":  (0.15,  0.075,  0.60),
    "gpt-4.1":      (2.00,  0.50,   8.00),
    "gpt-4.1-mini": (0.40,  0.10,   1.60),
    "gpt-4.1-nano": (0.10,  0.025,  0.40),
}

_GLM_PRICING: dict[str, tuple[float, float, float]] = {
    "glm-5":           (1.00, 0.20, 3.20),
    "glm-4.7":         (0.60, 0.11, 2.20),
    "glm-4.6":         (0.60, 0.11, 2.20),
    "glm-4.5":         (0.60, 0.11, 2.20),
    "glm-4.5-air":     (0.20, 0.04, 1.10),
    "glm-4.7-flashx":  (0.07, 0.01, 0.40),
    "glm-4.7-flash":   (0.00, 0.00, 0.00),
    "glm-4.5-flash":   (0.00, 0.00, 0.00),
}

# GLM: 国際 (Z.AI) / 中国本土 (bigmodel.cn) のエンドポイント
_GLM_BASE_URL = "https://api.z.ai/api/paas/v4/"
_GLM_BASE_URL_CN = "https://open.bigmodel.cn/api/paas/v4"

_WEB_SEARCH_COST_PER_CALL = 0.01  # $10 / 1,000 calls

# temperature パラメータ非対応モデル（GPT-5 系 / o 系の reasoning モデル）
_NO_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")

MAX_RETRIES = 3
_RETRY_DELAYS = [1, 3, 10]


@dataclass
class AgentResult:
    """call_openai / call_glm の戻り値。AgentUtil.AgentResult と同一構造。"""
    text: str = ""
    cost: float | None = None
    tools_used: list[str] = field(default_factory=list)


@dataclass
class AgentFileConfig:
    """エージェント定義ファイル (.md) のパース結果（プロバイダ非依存）。"""
    system_prompt: str = ""
    model: str | None = None
    metadata: dict = field(default_factory=dict)


def _load_env() -> None:
    env_path = _PROJECT_ROOT / ".env.local"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def parse_agent_file(file_path: str | Path) -> AgentFileConfig:
    """
    .md ファイルを読み込み、YAML フロントマターと本文を分離する。

    フロントマター内の model キーは AgentFileConfig.model に格納。
    マークダウン本文は system_prompt として使用。
    Claude Code 固有キー（tools, skills 等）は metadata に保持するが無視される。
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    if not content.startswith("---"):
        return AgentFileConfig(system_prompt=content)

    end_index = content.find("---", 3)
    if end_index == -1:
        return AgentFileConfig(system_prompt=content)

    frontmatter_str = content[3:end_index].strip()
    prompt_str = content[end_index + 3:].strip()

    frontmatter = yaml.safe_load(frontmatter_str) or {}
    model = frontmatter.pop("model", None)

    return AgentFileConfig(
        system_prompt=prompt_str,
        model=str(model) if model else None,
        metadata=frontmatter,
    )


def _calc_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    pricing: dict[str, tuple[float, float, float]],
) -> float | None:
    """トークン数とモデル単価から USD コストを算出。未知モデルは None。"""
    prices = pricing.get(model)
    if prices is None:
        return None
    input_price, cached_price, output_price = prices
    regular_input = prompt_tokens - cached_tokens
    return (
        regular_input * input_price
        + cached_tokens * cached_price
        + completion_tokens * output_price
    ) / 1_000_000


def _normalize_messages(
    messages: str | list[dict],
    file_path: str | Path | None,
) -> tuple[list[dict], AgentFileConfig | None]:
    """messages パラメータを OpenAI messages 形式に正規化する。"""
    config = None
    if file_path:
        config = parse_agent_file(file_path)

    if isinstance(messages, str):
        msg_list = [{"role": "user", "content": messages}]
    else:
        msg_list = list(messages)

    if config and config.system_prompt:
        msg_list.insert(0, {"role": "system", "content": config.system_prompt})

    return msg_list, config


async def _call_with_retry(coro_factory, provider_label: str):
    """レート制限・タイムアウト時にリトライする。"""
    from openai import RateLimitError, APITimeoutError, APIConnectionError

    retryable = (RateLimitError, APITimeoutError, APIConnectionError)
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return await coro_factory()
        except retryable as e:
            last_err = e
            if attempt == MAX_RETRIES - 1:
                raise
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            print(f"  [{provider_label} リトライ] {type(e).__name__}: {delay}秒後に再試行 ({attempt + 1}/{MAX_RETRIES})")
            await asyncio.sleep(delay)
    raise last_err  # type: ignore[misc]


async def call_openai(
    messages: str | list[dict],
    file_path: str | Path | None = None,
    model: str | None = None,
    tools: list[str] | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    show_prompt: bool = False,
    show_response: bool = False,
    show_cost: bool = True,
) -> AgentResult:
    """
    OpenAI の LLM を呼び出す。

    tools 未指定 → Chat Completions API（テキスト生成のみ）
    tools 指定   → Responses API（web_search 等のビルトインツール使用可能）

    Args:
        messages: プロンプト文字列、または OpenAI messages 形式の list[dict]
        file_path: エージェント定義ファイル (.md)。本文が system_prompt、
                   フロントマターの model がデフォルトモデルになる
        model: 使用モデル名。指定時はファイルの model より優先。
               未指定時は file_path の model → "gpt-5.2" の順でフォールバック
        tools: Responses API のビルトインツール名リスト。
               例: ["web_search"]
               指定すると Responses API に切り替わる
        temperature: サンプリング温度
        max_tokens: 最大出力トークン数
        show_prompt: リクエスト内容をコンソール出力
        show_response: レスポンスをコンソール出力
        show_cost: コストをコンソール出力

    Returns:
        AgentResult (text, cost, tools_used)
    """
    _load_env()
    from openai import AsyncOpenAI

    client = AsyncOpenAI()

    config = None
    if file_path:
        config = parse_agent_file(file_path)

    if model is None:
        model = (config.model if config and config.model else None) or "gpt-5.2"

    if tools:
        return await _call_openai_responses(
            client, messages, config, model, tools,
            temperature, max_tokens,
            show_prompt, show_response, show_cost,
        )
    return await _call_openai_chat(
        client, messages, config, model,
        temperature, max_tokens,
        show_prompt, show_response, show_cost,
    )


async def _call_openai_chat(
    client,
    messages: str | list[dict],
    config: AgentFileConfig | None,
    model: str,
    temperature: float,
    max_tokens: int | None,
    show_prompt: bool,
    show_response: bool,
    show_cost: bool,
) -> AgentResult:
    """Chat Completions API で呼び出す（ツールなし）。"""
    if isinstance(messages, str):
        msg_list = [{"role": "user", "content": messages}]
    else:
        msg_list = list(messages)

    if config and config.system_prompt:
        msg_list.insert(0, {"role": "system", "content": config.system_prompt})

    if show_prompt:
        print("╔══════════ リク (OpenAI Chat) ══════════")
        print(f"  model: {model}")
        for m in msg_list:
            role = m["role"]
            content = m["content"]
            preview = content[:200] + "..." if len(content) > 200 else content
            print(f"  [{role}] {preview}")
        print("╚══════════════════════════════════")

    kwargs: dict = {"model": model, "messages": msg_list}
    if not model.startswith(_NO_TEMPERATURE_PREFIXES):
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    response = await _call_with_retry(
        lambda: client.chat.completions.create(**kwargs),
        "OpenAI",
    )

    result = AgentResult()
    result.text = response.choices[0].message.content or ""

    if response.usage:
        cached = 0
        if hasattr(response.usage, "prompt_tokens_details") and response.usage.prompt_tokens_details:
            cached = getattr(response.usage.prompt_tokens_details, "cached_tokens", 0) or 0
        result.cost = _calc_cost(
            model,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            cached,
            _OPENAI_PRICING,
        )

    if show_response and result.text:
        print("╔══════════ レス (OpenAI Chat) ══════════")
        print(result.text)
        print("╚══════════════════════════════════")

    if show_cost and result.cost is not None:
        print(f"  [コスト] ${result.cost:.6f}")

    return result


async def _call_openai_responses(
    client,
    messages: str | list[dict],
    config: AgentFileConfig | None,
    model: str,
    tools: list[str],
    temperature: float,
    max_tokens: int | None,
    show_prompt: bool,
    show_response: bool,
    show_cost: bool,
) -> AgentResult:
    """Responses API で呼び出す（web_search 等のツール使用可能）。"""
    if isinstance(messages, str):
        prompt = messages
    else:
        prompt = "\n".join(m.get("content", "") for m in messages if m.get("role") != "system")

    instructions = config.system_prompt if config and config.system_prompt else None
    tool_defs = [{"type": t} for t in tools]

    if show_prompt:
        print("╔══════════ リク (OpenAI Responses) ══════════")
        print(f"  model: {model}")
        print(f"  tools: {tools}")
        if instructions:
            preview = instructions[:200] + "..." if len(instructions) > 200 else instructions
            print(f"  [instructions] {preview}")
        preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
        print(f"  [input] {preview}")
        print("╚══════════════════════════════════")

    kwargs: dict = {
        "model": model,
        "input": prompt,
        "tools": tool_defs,
    }
    if not model.startswith(_NO_TEMPERATURE_PREFIXES):
        kwargs["temperature"] = temperature
    if instructions:
        kwargs["instructions"] = instructions
    if max_tokens is not None:
        kwargs["max_output_tokens"] = max_tokens

    response = await _call_with_retry(
        lambda: client.responses.create(**kwargs),
        "OpenAI",
    )

    result = AgentResult()
    result.text = response.output_text or ""

    search_calls = 0
    for item in response.output:
        if item.type == "web_search_call":
            search_calls += 1
            result.tools_used.append("web_search")

    if response.usage:
        cached = 0
        if hasattr(response.usage, "input_tokens_details") and response.usage.input_tokens_details:
            cached = getattr(response.usage.input_tokens_details, "cached_tokens", 0) or 0
        token_cost = _calc_cost(
            model,
            response.usage.input_tokens,
            response.usage.output_tokens,
            cached,
            _OPENAI_PRICING,
        )
        search_cost = search_calls * _WEB_SEARCH_COST_PER_CALL
        if token_cost is not None:
            result.cost = token_cost + search_cost
        else:
            result.cost = search_cost if search_calls > 0 else None

    if show_response and result.text:
        print("╔══════════ レス (OpenAI Responses) ══════════")
        print(result.text)
        print("╚══════════════════════════════════")

    if show_cost and result.cost is not None:
        cost_detail = f"${result.cost:.6f}"
        if search_calls > 0:
            cost_detail += f" (検索{search_calls}回含む)"
        print(f"  [コスト] {cost_detail}")

    return result


async def call_glm(
    messages: str | list[dict],
    file_path: str | Path | None = None,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    show_prompt: bool = False,
    show_response: bool = False,
    show_cost: bool = True,
    base_url: str = _GLM_BASE_URL,
) -> AgentResult:
    """
    GLM (Zhipu AI / Z.AI) を OpenAI 互換エンドポイント経由で呼び出す。

    Args:
        messages: プロンプト文字列、または OpenAI messages 形式の list[dict]
        file_path: エージェント定義ファイル (.md)。本文が system_prompt、
                   フロントマターの model がデフォルトモデルになる
        model: 使用モデル名。指定時はファイルの model より優先。
               未指定時は file_path の model → "glm-4.7-flash" の順でフォールバック
        temperature: サンプリング温度
        max_tokens: 最大出力トークン数
        show_prompt: リクエスト内容をコンソール出力
        show_response: レスポンスをコンソール出力
        show_cost: コストをコンソール出力
        base_url: API エンドポイント（デフォルト: Z.AI 国際版）

    Returns:
        AgentResult (text, cost, tools_used=[])
    """
    _load_env()
    from openai import AsyncOpenAI

    api_key = os.environ.get("ZHIPUAI_API_KEY", "")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    msg_list, config = _normalize_messages(messages, file_path)

    if model is None:
        model = (config.model if config and config.model else None) or "glm-4.7-flash"

    if show_prompt:
        print("╔══════════ リク (GLM) ══════════")
        print(f"  model: {model}")
        for m in msg_list:
            role = m["role"]
            content = m["content"]
            preview = content[:200] + "..." if len(content) > 200 else content
            print(f"  [{role}] {preview}")
        print("╚══════════════════════════════════")

    kwargs: dict = {"model": model, "messages": msg_list, "temperature": temperature}
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    response = await _call_with_retry(
        lambda: client.chat.completions.create(**kwargs),
        "GLM",
    )

    result = AgentResult()
    result.text = response.choices[0].message.content or ""

    if response.usage:
        cached = 0
        if hasattr(response.usage, "prompt_tokens_details") and response.usage.prompt_tokens_details:
            cached = getattr(response.usage.prompt_tokens_details, "cached_tokens", 0) or 0
        result.cost = _calc_cost(
            model,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            cached,
            _GLM_PRICING,
        )

    if show_response and result.text:
        print("╔══════════ レス (GLM) ══════════")
        print(result.text)
        print("╚══════════════════════════════════")

    if show_cost and result.cost is not None:
        print(f"  [コスト] ${result.cost:.6f}")

    return result


if __name__ == "__main__":
    import sys

    async def _test():
        prompt = sys.argv[1] if len(sys.argv) > 1 else "Hello, who are you?"
        provider = sys.argv[2] if len(sys.argv) > 2 else "openai"

        if provider == "glm":
            r = await call_glm(prompt, show_response=True)
        else:
            r = await call_openai(prompt, show_response=True)

        print(f"\ntext length: {len(r.text)}")
        print(f"cost: {r.cost}")

    asyncio.run(_test())
