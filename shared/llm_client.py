"""
外部 LLM プロバイダー共有クライアント

call_agent()（Claude Agent SDK）と同等のインターフェースで
OpenAI / GLM を呼び出す。戻り値は AgentResult 互換。

OpenAI:
  - tools 未指定 → Chat Completions API（テキスト生成のみ）
  - tools 指定   → Responses API（web_search 等のツール使用可能）
GLM（2通りの呼び出し方）:
  call_glm       … AsyncOpenAI で OpenAI 互換エンドポイント（シンプルなテキスト生成）
  call_glm_agent … Claude Agent SDK + Z.AI の Anthropic 互換エンドポイント経由。
                   call_agent() と同一インターフェースで WebSearch 等のツール使用可能。

依存パッケージ:
    openai>=2.20          … call_openai / call_glm で使用
    claude-agent-sdk      … call_glm_agent で使用
    python-dotenv         … .env.local 読み込み
    pyyaml                … エージェントファイルのフロントマターパース

Usage:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
    from llm_client import call_openai, call_glm, call_glm_agent

    # テキスト生成のみ
    result = await call_openai("要約して")

    # ウェブ検索付き（Responses API）
    result = await call_openai("NVDAの最新株価は？", tools=["web_search"])

    # GLM（シンプル、ツールなし）
    result = await call_glm("NVDAを分析して", model="glm-4.7-flash")

    # GLM（call_agent 互換、WebSearch 等のツール使用可能）
    result = await call_glm_agent("NVDAを分析して")
    result = await call_glm_agent("NVDAを分析して", file_path=".claude/commands/analyst.md", model="glm-4")
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
    "gpt-5.4":      (2.50,  0.25,  15.00),
    "gpt-5.4-mini": (0.40,  0.04,   3.00),
    "gpt-5.4-nano": (0.10,  0.01,   0.40),
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
_GLM_BASE_URL = "https://api.z.ai/api/coding/paas/v4/"
_GLM_BASE_URL_CN = "https://open.bigmodel.cn/api/paas/v4"

_WEB_SEARCH_COST_PER_CALL = 0.01  # $10 / 1,000 calls

# temperature パラメータ非対応モデル（GPT-5 系 / o 系の reasoning モデル）
_NO_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")

MAX_RETRIES = 3
_RETRY_DELAYS = [1, 3, 10]
_GLM_RATELIMIT_DELAYS = [15, 30, 60]


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
    is_glm = provider_label == "GLM"
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            return await coro_factory()
        except retryable as e:
            last_err = e
            if attempt == MAX_RETRIES - 1:
                raise
            delays = _GLM_RATELIMIT_DELAYS if (is_glm and isinstance(e, RateLimitError)) else _RETRY_DELAYS
            delay = delays[min(attempt, len(delays) - 1)]
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


def _inline_file_refs(prompt: str) -> str:
    """
    プロンプト内の絶対ファイルパス（.md/.txt）を検出し、内容をプロンプト末尾に追記する。

    Claude Agent SDK の Read ツールに相当する機能を OpenAI 互換モードで代替する。
    同一パスは1回だけ読み込む。読み込み失敗時はパスをそのまま残す。
    """
    import re
    pattern = re.compile(r'(?:[A-Za-z]:[/\\]|/)[\w/\\.\-]+\.(?:md|txt)', re.IGNORECASE)

    found: dict[str, Path] = {}
    for match in pattern.finditer(prompt):
        path_str = match.group(0)
        if path_str in found:
            continue
        try:
            p = Path(path_str)
            if p.exists() and p.is_file():
                found[path_str] = p
        except Exception:
            pass

    if not found:
        return prompt

    parts = [prompt, "\n\n===== 参照ファイル内容（自動展開） ====="]
    for path_str, p in found.items():
        try:
            content = p.read_text(encoding="utf-8")
            parts.append(f"\n--- {path_str} ---\n{content}\n--- ここまで ---")
        except Exception as e:
            parts.append(f"\n--- {path_str} ---\n(読み込み失敗: {e})\n---")
    parts.append("===== ここまで =====")
    return "\n".join(parts)


async def call_glm_agent(
    messages,
    file_path: str | Path | None = None,
    model: str = "glm-4.7-flash",
    show_options: bool = False,
    show_prompt: bool = False,
    show_response: bool = False,
    show_cost: bool = True,
    show_tools: bool = False,
) -> AgentResult:
    """
    GLM (Z.AI) を OpenAI 互換エンドポイント経由で呼び出す。call_agent() と同等のインターフェース。

    Claude Agent SDK は使わず Z.AI の OpenAI 互換エンドポイントを直接呼ぶ。
    プロンプト内の絶対ファイルパス参照は自動でインライン展開する（Read ツール相当）。
    エージェント定義に WebSearch が含まれる場合は Z.AI ネイティブ web_search ツールを使用する。

    Args:
        messages: プロンプト文字列、list[dict]（OpenAI形式）、または非同期イテレーター
        file_path: エージェント定義ファイル (.md)。本文が system_prompt、
                   フロントマターの tools が使用ツール判定に使われる
        model: GLM モデル名（"glm-4.7-flash", "glm-4.7", "glm-5" 等）
        show_options: オプション内容をコンソール出力
        show_prompt: プロンプトをコンソール出力
        show_response: レスポンスをコンソール出力
        show_cost: コストをコンソール出力
        show_tools: 使用ツールをコンソール出力

    Returns:
        AgentResult (text, cost, tools_used)
    """
    _load_env()
    from openai import AsyncOpenAI

    api_key = os.environ.get("ZHIPUAI_API_KEY", "")
    client = AsyncOpenAI(api_key=api_key, base_url=_GLM_BASE_URL)

    # プロンプト文字列に正規化
    if isinstance(messages, str):
        prompt = messages
    elif isinstance(messages, list):
        prompt = "\n".join(
            m.get("content", "") for m in messages if m.get("role") != "system"
        )
    else:
        parts: list[str] = []
        async for msg in messages:
            if isinstance(msg, str):
                parts.append(msg)
        prompt = "\n".join(parts)

    # エージェント定義ファイルを読み込む
    config = parse_agent_file(file_path) if file_path else None

    # プロンプト内のファイルパス参照をインライン展開（Read ツール相当）
    prompt = _inline_file_refs(prompt)

    # メッセージリストを構築
    msg_list: list[dict] = [{"role": "user", "content": prompt}]
    if config and config.system_prompt:
        msg_list.insert(0, {"role": "system", "content": config.system_prompt})

    # エージェント定義に WebSearch が含まれれば Z.AI ネイティブ web_search を有効化
    agent_tools: list[str] = config.metadata.get("tools", []) if config else []
    use_web_search = any(t in ("WebSearch", "web_search") for t in agent_tools)

    if show_prompt or show_options:
        print("╔══════════ リク (GLM OpenAI) ══════════")
        if show_options:
            print(f"  model: {model}")
            print(f"  base_url: {_GLM_BASE_URL}")
            print(f"  web_search: {'enabled' if use_web_search else 'disabled'}")
        if show_prompt:
            if config and config.system_prompt:
                preview = config.system_prompt[:200] + "..." if len(config.system_prompt) > 200 else config.system_prompt
                print(f"  [system] {preview}")
            preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
            print(f"  [prompt] {preview}")
        print("╚══════════════════════════════════")

    kwargs: dict = {"model": model, "messages": msg_list, "temperature": 0.7}
    if use_web_search:
        kwargs["tools"] = [{"type": "web_search", "web_search": {"enable": True}}]

    response = await _call_with_retry(
        lambda: client.chat.completions.create(**kwargs),
        "GLM",
    )

    result = AgentResult()
    result.text = response.choices[0].message.content or ""

    if use_web_search:
        result.tools_used.append("web_search")
        if show_tools:
            print("[ツール: web_search]")

    if response.usage:
        result.cost = _calc_cost(
            model,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            0,
            _GLM_PRICING,
        )

    if show_response and result.text:
        print("╔══════════ レス (GLM OpenAI) ══════════")
        print(result.text)
        print("╚══════════════════════════════════")

    if show_cost and result.cost is not None:
        print(f"  [コスト] ${result.cost:.6f}")

    return result


async def call_codex(
    messages,
    file_path: str | Path | None = None,
    show_prompt: bool = False,
    show_response: bool = False,
    show_cost: bool = True,
    show_tools: bool = False,
) -> AgentResult:
    """
    Codex SDK (ChatGPT Plus 枠) でプロンプトを実行する。

    codex CLI バイナリを子プロセスとして起動し、ChatGPT Plus のサブスクリプション枠で
    GPT-5.4 等のモデルを利用する。モデルや reasoning effort は ~/.codex/config.toml で設定。

    Args:
        messages: プロンプト文字列、list[dict]（OpenAI形式）、または非同期イテレーター
        file_path: エージェント定義ファイル (.md)。本文が system prompt として
                   プロンプトの先頭に注入される
        show_prompt: プロンプトをコンソール出力
        show_response: レスポンスをコンソール出力
        show_cost: コスト情報をコンソール出力（Codex SDK ではサブスク枠のため常に $0）
        show_tools: 使用ツールをコンソール出力

    Returns:
        AgentResult (text, cost=0, tools_used)
    """
    from openai_codex_sdk import Codex
    from openai_codex_sdk.codex import CodexOptions
    import shutil

    codex_exe = shutil.which("codex")
    if codex_exe is None:
        codex_exe = shutil.which("codex.exe")

    config = parse_agent_file(file_path) if file_path else None

    if isinstance(messages, str):
        prompt = messages
    elif isinstance(messages, list):
        prompt = "\n".join(
            m.get("content", "") for m in messages if m.get("role") != "system"
        )
    else:
        parts: list[str] = []
        async for msg in messages:
            if isinstance(msg, str):
                parts.append(msg)
        prompt = "\n".join(parts)

    prompt = _inline_file_refs(prompt)

    system_prompt = config.system_prompt if config else ""
    if system_prompt:
        full_prompt = f"# 指示\n\n{system_prompt}\n\n# タスク\n\n{prompt}"
    else:
        full_prompt = prompt

    if show_prompt:
        print("╔══════════ リク (Codex SDK) ══════════")
        if system_prompt:
            preview = system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt
            print(f"  [system] {preview}")
        preview = prompt[:200] + "..." if len(prompt) > 200 else prompt
        print(f"  [prompt] {preview}")
        print("╚══════════════════════════════════")

    opts = CodexOptions(codex_path_override=codex_exe) if codex_exe else CodexOptions()
    codex = Codex(opts)
    thread = codex.start_thread()

    for attempt in range(MAX_RETRIES):
        try:
            turn = await thread.run(full_prompt)
            break
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                raise
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            print(f"  [Codex リトライ] {type(e).__name__}: {delay}秒後に再試行 ({attempt + 1}/{MAX_RETRIES})")
            await asyncio.sleep(delay)

    result = AgentResult()
    result.text = turn.final_response or ""
    result.cost = 0.0

    if show_response and result.text:
        print("╔══════════ レス (Codex SDK) ══════════")
        print(result.text)
        print("╚══════════════════════════════════")

    if show_cost:
        print("  [コスト] ChatGPT Plus 枠（追加料金なし）")

    return result


if __name__ == "__main__":
    import sys

    async def _test():
        prompt = sys.argv[1] if len(sys.argv) > 1 else "Hello, who are you?"
        provider = sys.argv[2] if len(sys.argv) > 2 else "openai"

        if provider == "glm":
            r = await call_glm(prompt, show_response=True)
        elif provider == "glm-agent":
            r = await call_glm_agent(prompt, show_response=True, show_tools=True)
        elif provider == "codex":
            r = await call_codex(prompt, show_response=True)
        else:
            r = await call_openai(prompt, show_response=True)

        print(f"\ntext length: {len(r.text)}")
        print(f"cost: {r.cost}")

    asyncio.run(_test())
