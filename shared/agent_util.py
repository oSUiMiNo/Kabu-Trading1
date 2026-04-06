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
_quota_notified_providers: set[str] = set()

_QUOTA_LIMIT_PATTERNS = [
    "usage limit",
    "rate limit",
    "quota exceeded",
    "insufficient_quota",
    "purchase more credits",
]

_RESET_TIME_RE = re.compile(
    r"try again (?:at|after)\s+(.+?)(?:\.|$)", re.IGNORECASE
)


class LLMQuotaError(RuntimeError):
    """LLM プロバイダーのクォータ上限エラー。"""
    def __init__(self, provider: str, agent_name: str, original_error: Exception):
        self.provider = provider
        self.agent_name = agent_name
        self.original_error = original_error
        super().__init__(str(original_error))


def _is_quota_error(error: Exception) -> bool:
    """エラーメッセージがクォータ/使用量上限に該当するか判定する。"""
    msg = str(error).lower()
    return any(p in msg for p in _QUOTA_LIMIT_PATTERNS)


def _extract_reset_time(error: Exception) -> str | None:
    """エラーメッセージからリセット日時を抽出する。"""
    m = _RESET_TIME_RE.search(str(error))
    return m.group(1).strip() if m else None


def _notify_quota_limit(provider: str, agent_name: str, error: Exception) -> None:
    """クォータ上限を Discord に通知する（provider ごとに1回のみ）。"""
    if provider in _quota_notified_providers:
        return
    _quota_notified_providers.add(provider)

    reset_time = _extract_reset_time(error)
    reset_line = f"\n⏰ リセット予定：**{reset_time}**" if reset_time else ""

    try:
        from discord_notifier import send_webhook
        embed = {
            "title": "🚫 LLM クォータ上限到達",
            "description": (
                f"**{provider}** のクォータ上限に達しました。\n"
                f"この provider を使う全エージェントが影響を受けます。"
                f"{reset_line}\n\n"
                f"`config/portfolio_config.yml` の `llm_providers` で "
                f"該当エージェントの provider を一時的に変更するか、"
                f"リセットをお待ちください。"
            ),
            "color": 0xFF0000,
            "fields": [
                {"name": "検出元", "value": agent_name, "inline": True},
                {"name": "Provider", "value": provider, "inline": True},
            ],
        }
        send_webhook(embed)
    except Exception as e:
        print(f"  [通知] クォータ上限の Discord 通知に失敗: {e}")


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
            if _is_quota_error(e):
                _notify_quota_limit(_provider, _agent_name, e)
                raise LLMQuotaError(_provider, _agent_name, e) from e
            raise
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
            if _is_quota_error(e):
                _notify_quota_limit(_provider, _agent_name, e)
                raise LLMQuotaError(_provider, _agent_name, e) from e
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
