"""
Claude Agent SDK 共通ユーティリティ（統合版）

各モジュール（Analyzer, Monitor, Planning, EventScheduler, Watch）の
AgentUtil.py から共通ロジックを抽出したもの。
各モジュールの AgentUtil.py はこのファイルを参照する薄いラッパーとして残す。

LLM プロバイダーは config/portfolio_config.yml の llm_providers で一括管理する。
未記載のエージェントは Discord 通知を送信しエラーとなる。
"""
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import yaml
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
    ClaudeAgentOptions,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


_SIDE_JA: dict[str, str] = {
    "BUY": "買い",
    "SELL": "売り",
    "ADD": "買い増し",
    "REDUCE": "売り減らし",
    "HOLD": "現状維持",
}


def side_ja(side: str) -> str:
    """supported_side 等の英語判定値を日本語表示に変換する"""
    clean = side.strip("*").strip()
    ja = _SIDE_JA.get(clean)
    if ja is None:
        return side
    if side.startswith("**") and side.endswith("**"):
        return f"**{ja}**"
    return ja


def load_debug_config(phase: str, project_root: Path | None = None) -> dict:
    """
    debug_config.yaml から指定フェーズの表示設定を読み込む。

    Args:
        phase: フェーズ名（例: "monitor", "analyzer"）
        project_root: モジュールのルートパス（debug_config.yaml の親ディレクトリ）
    """
    defaults = {"show_options": False, "show_prompt": False, "show_response": False, "show_cost": False}
    if project_root is None:
        return defaults
    config_path = project_root / "debug_config.yaml"
    if not config_path.exists():
        return defaults
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    section = data.get(phase, {})
    return {k: section.get(k, False) for k in defaults}


@dataclass
class AgentResult:
    """call_agent()の戻り値"""
    text: str = ""
    cost: float | None = None
    tools_used: list[str] = field(default_factory=list)


def save_result_log(result: "AgentResult", log_path: str | Path, append: bool = False) -> Path | None:
    """
    AgentResult.text をログファイルに書き出す。

    Args:
        result: call_agent() の戻り値
        log_path: 書き出し先のファイルパス
        append: True なら既存ファイルの末尾に追記
    """
    if not result or not result.text:
        return None
    p = Path(log_path)
    if append and p.exists():
        existing = p.read_text(encoding="utf-8")
        p.write_text(existing + result.text, encoding="utf-8")
    else:
        p.write_text(result.text, encoding="utf-8")
    return p


def extract_text(message) -> str | None:
    """AssistantMessageからテキストを抽出"""
    if isinstance(message, AssistantMessage):
        texts = []
        for block in message.content:
            if isinstance(block, TextBlock):
                texts.append(block.text)
        return "\n".join(texts) if texts else None
    return None


def extract_cost(message) -> float | None:
    """ResultMessageからコストを抽出"""
    if isinstance(message, ResultMessage):
        return message.total_cost_usd
    return None


def extract_tool_use(message) -> list[str]:
    """AssistantMessageから使用ツール名を抽出"""
    tools = []
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                tools.append(block.name)
    return tools


def parse_agent_file(file_path: str | Path) -> ClaudeAgentOptions:
    """
    ファイルをパースしてClaudeAgentOptionsを返す。

    - フロントマター付き: YAMLからoptions、Markdown本文をsystem_promptに
    - フロントマター無し: ファイル内容全体をsystem_promptに
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    if not content.startswith("---"):
        return ClaudeAgentOptions(system_prompt=content)

    end_index = content.find("---", 3)
    if end_index == -1:
        return ClaudeAgentOptions(system_prompt=content)

    frontmatter_str = content[3:end_index].strip()
    prompt_str = content[end_index + 3 :].strip()

    frontmatter = yaml.safe_load(frontmatter_str) or {}

    kwargs = {}

    if "tools" in frontmatter:
        kwargs["allowed_tools"] = frontmatter["tools"]
    elif "allowed-tools" in frontmatter:
        kwargs["allowed_tools"] = frontmatter["allowed-tools"]

    exclude_keys = {
        "name", "description", "version",
        "tools", "allowed-tools",
        "skills", "inherits",
        "disable-model-invocation", "user-invocable",
        "context", "provider",
    }
    for key, value in frontmatter.items():
        if key not in exclude_keys:
            kwargs[key] = value

    kwargs["system_prompt"] = prompt_str

    return ClaudeAgentOptions(**kwargs)


_CONFIG_DIR = _PROJECT_ROOT / "config"
_llm_providers_cache: dict[str, str] | None = None

# ── フォールバック関連 ──
#
# LLM の使用量上限（クォータ）に達した場合、自動的に OpenAI API に切り替える仕組み。
#
# 動作の流れ：
#   1. codex や glm を呼んで「使用量上限」エラーが返ってきたら
#   2. その provider を「このパイプライン実行中は使えない」とマークし
#   3. 代わりに OpenAI API（GPT-5.4）で同じ処理を実行する
#   4. 次のパイプライン実行（別プロセス）ではマークがリセットされ、元の provider を再び試す
#
# 3つの set はこのプロセスが生きている間だけ有効。ファイルや DB には保存しない。

_run_quota_exhausted: set[str] = set()
# 「この provider は上限に達した」を記録する。
# ここに入っている provider は、同じパイプライン実行中は二度と試さず、直接 OpenAI に回す。
# 例：{"codex"} → codex を使うエージェントは全て OpenAI にフォールバック

_run_fallback_failed: set[str] = set()
# 「OpenAI へのフォールバックが認証エラー等の永続的失敗をした」を記録する。
# ここに入っている provider は、同じ実行中はフォールバックすら試さず即エラーにする。
# API キー不備など、リトライしても回復しないエラーのみを記録する。
# 一時的な障害（タイムアウト・レート制限等）は記録せず、リトライ時に再試行させる。

_run_notified: set[str] = set()
# 「この provider の上限到達を Discord に通知済み」を記録する。
# 同じパイプライン実行中に同じ provider の通知が何度も飛ぶのを防ぐ。
# ※ プロセス内の重複防止。プロセス間（バッチ並列実行）の重複防止は
#    _FALLBACK_NOTIFY_FLAG_DIR のファイルフラグで行う。

_FALLBACK_NOTIFY_FLAG_DIR = Path(tempfile.gettempdir()) / "llm_fallback_flags"

# ── エラーメッセージの判定パターン ──

# 「使用量の上限に達した」系のエラー → OpenAI にフォールバックする対象
_FALLBACK_PATTERNS = [
    "usage limit",
    "quota exceeded",
    "insufficient_quota",
    "purchase more credits",
]

# 「一時的にリクエストが多すぎる」系のエラー → しばらく待てば回復するのでフォールバックしない
_TRANSIENT_RATE_PATTERNS = [
    "rate limit",
    "too many requests",
]

# OpenAI フォールバック時に「リトライしても回復しない」と判断するパターン。
# これに該当するエラーのみ _run_fallback_failed に記録し、以降の試行をブロックする。
_PERMANENT_FAILURE_PATTERNS = [
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "authorization",
    "permission denied",
    "account deactivated",
]

_RESET_TIME_RE = re.compile(
    r"try again (?:at|after)\s+(.+?)(?:\.|$)", re.IGNORECASE
)


class LLMQuotaError(RuntimeError):
    """使用量上限エラー。エラーの発生元（provider 名、エージェント名）を保持する。"""
    def __init__(self, provider: str, agent_name: str, original_error: Exception):
        self.provider = provider
        self.agent_name = agent_name
        self.original_error = original_error
        super().__init__(str(original_error))


class LLMFallbackError(RuntimeError):
    """フォールバック先の OpenAI API も失敗した場合のエラー。
    リトライしても同じ結果になるため、呼び出し元はこれを受け取ったら諦める。"""
    pass


def _is_fallback_target(error: Exception) -> bool:
    """このエラーは「使用量の上限」か？（→ Yes なら OpenAI にフォールバック）
    「一時的にリクエストが多い」（rate limit）は対象外。"""
    msg = str(error).lower()
    return any(p in msg for p in _FALLBACK_PATTERNS)


def _is_quota_error(error: Exception) -> bool:
    """このエラーは使用量関連か？（上限 + 一時的レート制限の両方を含む、後方互換用）"""
    msg = str(error).lower()
    return _is_fallback_target(error) or any(p in msg for p in _TRANSIENT_RATE_PATTERNS)


def _extract_reset_time(error: Exception) -> str | None:
    """エラーメッセージからリセット日時を抽出する。"""
    m = _RESET_TIME_RE.search(str(error))
    return m.group(1).strip() if m else None


def _normalize_fallback_output(text: str) -> str:
    """OpenAI フォールバックの出力を正規化する。
    GPT 系モデルはコードフェンスなしで bare YAML を返すことがあるため、
    コードフェンスが無く YAML らしき内容がある場合は ```yaml ``` で囲む。"""
    if not text or "```" in text:
        return text
    if re.search(r"^\w[\w_]*:", text, re.MULTILINE):
        return f"```yaml\n{text.strip()}\n```"
    return text


def _extract_tools_from_agent(file_path: str | Path | None) -> list[str]:
    """エージェント定義ファイル（.md）の frontmatter を読み、
    OpenAI API で使える tools（例：web_search）のリストを返す。
    フォールバック時に「このエージェントは Web 検索が必要か？」を判定するために使う。"""
    if not file_path:
        return []
    try:
        options = parse_agent_file(file_path)
    except Exception:
        return []
    if not options or not getattr(options, "allowed_tools", None):
        return []
    openai_tools: list[str] = []
    for t in options.allowed_tools:
        if t in ("WebSearch", "WebFetch", "web_search"):
            if "web_search" not in openai_tools:
                openai_tools.append("web_search")
    return openai_tools


def _load_fallback_config() -> dict:
    """フォールバック先の設定（モデル名と推論強度）を portfolio_config.yml から読む。
    config に未記載の場合は GPT-5.4 / high をデフォルトとして使う。"""
    config_path = _CONFIG_DIR / "portfolio_config.yml"
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return {
            "model": data.get("fallback_openai_model", "gpt-5.4"),
            "reasoning_effort": data.get("fallback_openai_reasoning_effort", "high"),
        }
    return {"model": "gpt-5.4", "reasoning_effort": "high"}


def clear_fallback_notify_flags() -> None:
    """フォールバック通知フラグをリセットする。ブロック間で呼び出す。"""
    if _FALLBACK_NOTIFY_FLAG_DIR.exists():
        for f in _FALLBACK_NOTIFY_FLAG_DIR.iterdir():
            f.unlink(missing_ok=True)


def _notify_quota_fallback(
    provider: str, agent_name: str, reset_time: str | None, fallback_model: str,
) -> None:
    """クォータ上限 + フォールバック実行を Discord に通知（provider ごとにブロック内1回のみ）。"""
    if provider in _run_notified:
        return
    _run_notified.add(provider)

    _FALLBACK_NOTIFY_FLAG_DIR.mkdir(exist_ok=True)
    flag = _FALLBACK_NOTIFY_FLAG_DIR / f"{provider}.flag"
    try:
        fd = os.open(str(flag), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return
    reset_display = reset_time or "不明"

    try:
        from discord_notifier import send_webhook
        embed = {
            "title": "\U0001f504 LLM クォータ上限 → 自動切替",
            "description": (
                f"**{provider}** のクォータ上限に達したため、"
                f"**OpenAI API ({fallback_model})** に切り替えて実行します。"
            ),
            "color": 0xFFA500,
            "fields": [
                {"name": "検出元", "value": agent_name, "inline": True},
                {"name": "解除予定", "value": reset_display, "inline": True},
                {"name": "切替先", "value": f"OpenAI API ({fallback_model})", "inline": True},
            ],
        }
        send_webhook(embed)
    except Exception as e:
        print(f"  [通知] フォールバック通知に失敗: {e}")


async def _fallback_to_openai(
    messages,
    file_path: str | Path | None,
    agent_name: str,
    original_provider: str,
    quota_error: Exception | None,
    tools: list[str] | None = None,
    show_prompt: bool = False,
    show_response: bool = False,
    show_cost: bool = True,
) -> AgentResult:
    """元の provider（codex/glm）が使用量上限に達した場合に、OpenAI API で代わりに実行する。
    - 初回呼び出し：OpenAI を試し、成功すれば結果を返す。失敗すれば記録して LLMFallbackError。
    - 2回目以降（同じ provider）：既に失敗済みなので OpenAI を試さず即エラー。"""
    if original_provider in _run_fallback_failed:
        raise LLMFallbackError(f"{original_provider} の fallback は既に失敗済み")

    fb_cfg = _load_fallback_config()
    fb_model = fb_cfg["model"]
    fb_reasoning = fb_cfg["reasoning_effort"]
    reset_time = _extract_reset_time(quota_error) if quota_error else None

    print(f"  [{agent_name}] {original_provider} quota → OpenAI {fb_model} にフォールバック")
    _notify_quota_fallback(original_provider, agent_name, reset_time, fb_model)

    _shared = Path(__file__).resolve().parent
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from llm_client import call_openai

    try:
        _r = await call_openai(
            messages,
            file_path=file_path,
            model=fb_model,
            tools=tools or None,
            reasoning_effort=fb_reasoning,
            show_prompt=show_prompt,
            show_response=show_response,
            show_cost=show_cost,
        )
        text = _normalize_fallback_output(_r.text)
        return AgentResult(text=text, cost=_r.cost, tools_used=list(_r.tools_used))
    except Exception as fallback_err:
        err_msg = str(fallback_err).lower()
        if any(p in err_msg for p in _PERMANENT_FAILURE_PATTERNS):
            _run_fallback_failed.add(original_provider)
        raise LLMFallbackError(
            f"{original_provider} → OpenAI fallback 失敗: {fallback_err}"
        ) from fallback_err


def _notify_unconfigured_provider(agent_name: str) -> None:
    """provider 未設定のエージェントについて Discord に通知する。"""
    try:
        from discord_notifier import send_webhook
        embed = {
            "title": "⚙️ LLM Provider 未設定",
            "description": (
                f"エージェント **{agent_name}** の provider が "
                f"`config/portfolio_config.yml` に設定されていません。\n"
                f"LLMの設定をしてください。"
            ),
            "color": 0xFFA500,
        }
        send_webhook(embed)
    except Exception as e:
        print(f"  [通知] provider 未設定の Discord 通知に失敗: {e}")


def _load_llm_providers() -> dict[str, str]:
    """portfolio_config.yml の llm_providers セクションを読み込む（キャッシュ付き）。"""
    global _llm_providers_cache
    if _llm_providers_cache is not None:
        return _llm_providers_cache
    config_path = _CONFIG_DIR / "portfolio_config.yml"
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        _llm_providers_cache = data.get("llm_providers") or {}
    else:
        _llm_providers_cache = {}
    return _llm_providers_cache


def _detect_provider(file_path: str | Path | None) -> tuple[str | None, str | None]:
    """
    エージェントの LLM プロバイダーと model を決定する。

    provider は portfolio_config.yml の llm_providers のみで決定する。
    yaml に未記載のエージェントは None を返す（呼び出し側でエラー処理）。
    model は .md ファイルの frontmatter から読み取る。

    Returns:
        (provider, model) — provider は未記載なら None、model は .md に明示されていなければ None
    """
    file_model = None
    provider = None

    if file_path:
        agent_name = Path(file_path).stem
        providers_map = _load_llm_providers()
        provider = providers_map.get(agent_name)

        try:
            content = Path(file_path).read_text(encoding="utf-8")
            if content.startswith("---"):
                end_idx = content.find("---", 3)
                if end_idx != -1:
                    fm = yaml.safe_load(content[3:end_idx].strip()) or {}
                    file_model = fm.get("model")
        except Exception:
            pass

    return provider.lower() if provider else None, str(file_model) if file_model else None


async def call_agent(
    messages,
    file_path: str | Path | None = None,
    show_options: bool = False,
    show_prompt: bool = False,
    show_response: bool = False,
    show_cost: bool = True,
    show_tools: bool = False,
    project_root: Path | None = None,
    src_dir: Path | None = None,
) -> AgentResult:
    """
    プロンプトを受け取り、LLMに渡して応答を表示し、結果を返す。

    provider は portfolio_config.yml の llm_providers で決定する。
    未記載のエージェントは Discord 通知を送信し RuntimeError を送出する。

    Args:
        messages: 文字列または非同期イテレーター
        file_path: エージェント定義ファイルのパス
        project_root: モジュールのルートパス（debug_config.yaml の探索先）
        src_dir: モジュールの src/ パス（common-prompt.md の探索先）
    """
    _provider, _file_model = _detect_provider(file_path)

    if _provider is None:
        agent_name = Path(file_path).stem if file_path else "(unknown)"
        _notify_unconfigured_provider(agent_name)
        raise RuntimeError(
            f"エージェント '{agent_name}' の provider が portfolio_config.yml に未設定です。"
            f" llm_providers セクションに追加してください。"
        )

    _agent_name = Path(file_path).stem if file_path else "(unknown)"
    _agent_tools = _extract_tools_from_agent(file_path)  # WebSearch 等が必要か事前に読む

    # この provider は既にこの実行中に上限に達している → 元の LLM は試さず直接 OpenAI へ
    if _provider in _run_quota_exhausted:
        return await _fallback_to_openai(
            messages, file_path, _agent_name, _provider, None,
            tools=_agent_tools,
            show_prompt=show_prompt, show_response=show_response, show_cost=show_cost,
        )

    if _provider == "codex":
        _shared = Path(__file__).resolve().parent
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from llm_client import call_codex as _call_codex
        try:
            _r = await _call_codex(
                messages, file_path=file_path,
                show_prompt=show_prompt, show_response=show_response,
                show_cost=show_cost, show_tools=show_tools,
            )
        except Exception as e:
            if _is_fallback_target(e):
                # 使用量上限エラー → この provider を「使えない」とマークして OpenAI に切替
                _run_quota_exhausted.add(_provider)
                return await _fallback_to_openai(
                    messages, file_path, _agent_name, _provider, e,
                    tools=_agent_tools,
                    show_prompt=show_prompt, show_response=show_response, show_cost=show_cost,
                )
            raise  # 上限以外のエラーはそのまま上位に伝える
        return AgentResult(text=_r.text, cost=_r.cost, tools_used=list(_r.tools_used))

    if _provider == "glm":
        _shared = Path(__file__).resolve().parent
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        from llm_client import call_glm_agent as _call_glm_agent
        _glm_model = _file_model or os.environ.get("ANALYZER_GLM_MODEL", "glm-4.7-flash")
        try:
            _r = await _call_glm_agent(
                messages, file_path=file_path, model=_glm_model,
                show_options=show_options, show_prompt=show_prompt,
                show_response=show_response, show_cost=show_cost, show_tools=show_tools,
            )
        except Exception as e:
            if _is_fallback_target(e):
                _run_quota_exhausted.add(_provider)
                return await _fallback_to_openai(
                    messages, file_path, _agent_name, _provider, e,
                    tools=_agent_tools,
                    show_prompt=show_prompt, show_response=show_response, show_cost=show_cost,
                )
            raise
        return AgentResult(text=_r.text, cost=_r.cost, tools_used=list(_r.tools_used))

    if isinstance(messages, str):
        prompt = messages
    else:
        prompt_parts = []
        async for msg in messages:
            if isinstance(msg, str):
                prompt_parts.append(msg)
            elif isinstance(msg, AssistantMessage):
                text = extract_text(msg)
                if text:
                    prompt_parts.append(text)
        prompt = "\n".join(prompt_parts)

    options = parse_agent_file(file_path) if file_path else None

    debug_prompt = ""
    if project_root:
        debug_config_path = project_root / "debug_config.yaml"
        if debug_config_path.exists():
            _cfg = yaml.safe_load(debug_config_path.read_text(encoding="utf-8")) or {}
            debug_prompt = (_cfg.get("debug_prompt") or "").strip()

    common_prompt = ""
    if src_dir and options:
        common_prompt_path = src_dir / "common-prompt.md"
        if common_prompt_path.exists():
            common_prompt = common_prompt_path.read_text(encoding="utf-8").strip()

    agent_system_prompt = options.system_prompt if options else ""

    if options:
        parts = [p for p in [debug_prompt, common_prompt, agent_system_prompt] if p]
        options.system_prompt = "\n\n".join(parts)

    if show_prompt or show_options:
        print("╔══════════ リク ══════════")
        if show_options and options:
            _skip = {"system_prompt", "debug_stderr"}
            print("── オプション ──")
            for key, value in vars(options).items():
                if key in _skip:
                    continue
                if value is None or value == [] or value == {} or value is False:
                    continue
                print(f"  {key}: {value}")
            print()
        if show_prompt:
            print("── デバッグプロンプト ──")
            print(debug_prompt if debug_prompt else "(なし)")
            print()
            print("── 共通システムプロンプト ──")
            print(common_prompt if common_prompt else "(なし)")
            print()
            print("── システムプロンプト ──")
            print(agent_system_prompt if agent_system_prompt else "(なし)")
            print()
            print("── プロンプト ──")
            print(prompt)
            print()
        print("╚══════════════════════════")

    result = AgentResult()

    text_parts = []
    async for response in query(prompt=prompt, options=options):
        text = extract_text(response)
        if text:
            text_parts.append(text)

        tools = extract_tool_use(response)
        if tools:
            result.tools_used.extend(tools)
            if show_tools:
                for tool in tools:
                    print(f"[ツール: {tool}]")

        cost = extract_cost(response)
        if cost:
            result.cost = cost

    result.text = "\n".join(text_parts)

    if show_response and result.text:
        print("╔══════════ レス ══════════")
        print(result.text)
        print("╚══════════════════════════")

    return result
