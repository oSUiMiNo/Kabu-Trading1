"""
Claude Agent SDK 共通ユーティリティ
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Windows環境でcp932によるUnicodeエンコードエラーを防止
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


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEBUG_CONFIG_PATH = PROJECT_ROOT / "debug_config.yaml"


def load_debug_config(phase: str) -> dict:
    """
    debug_config.yaml から指定フェーズの表示設定を読み込む。

    Returns:
        {"show_options": bool, "show_prompt": bool, "show_response": bool}
        ファイルが無い or フェーズが未定義なら全て False。
    """
    defaults = {"show_options": False, "show_prompt": False, "show_response": False}
    if not DEBUG_CONFIG_PATH.exists():
        return defaults
    data = yaml.safe_load(DEBUG_CONFIG_PATH.read_text(encoding="utf-8")) or {}
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

    エージェントの応答テキストをそのままファイルに保存するユーティリティ。
    opinion→judge→final-judge 等、エージェントがテキスト応答を返し
    オーケストレーター側でファイル化するフローで共通利用する。

    Args:
        result: call_agent() の戻り値
        log_path: 書き出し先のファイルパス
        append: True なら既存ファイルの末尾に追記（議論ログのラウンド追記用）

    Returns:
        書き出したPathオブジェクト。result.text が空なら None。
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
    メインエージェント用の設定として返す（サブエージェントではない）。

    - フロントマター付き: YAMLからoptions、Markdown本文をsystem_promptに
    - フロントマター無し: ファイル内容全体をsystem_promptに

    Args:
        file_path: 読み込むファイルのパス

    Returns:
        ClaudeAgentOptions: system_prompt（必須）、その他オプション（フロントマターから）

    Usage:
        from utils import parse_agent_file

        # フロントマター付き
        options = parse_agent_file(".claude/commands/analyst.md")
        # options.allowed_tools → ["Read", "Write", ...]
        # options.system_prompt → "# Analyst（考察サブエージェント）\n..."

        # フロントマター無し
        options = parse_agent_file("prompt.txt")
        # options.system_prompt → ファイル内容全体
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    # フロントマター無し → ファイル内容全体をsystem_promptとして返す
    if not content.startswith("---"):
        return ClaudeAgentOptions(system_prompt=content)

    # 2つ目の --- を探す
    end_index = content.find("---", 3)
    if end_index == -1:
        # フロントマターの終端が見つからない → 通常ファイルとして扱う
        return ClaudeAgentOptions(system_prompt=content)

    frontmatter_str = content[3:end_index].strip()
    prompt_str = content[end_index + 3 :].strip()

    # YAMLをパース
    frontmatter = yaml.safe_load(frontmatter_str) or {}

    # ClaudeAgentOptionsに渡すkwargsを構築
    kwargs = {}

    # tools / allowed-tools → allowed_tools にマッピング
    if "tools" in frontmatter:
        kwargs["allowed_tools"] = frontmatter["tools"]
    elif "allowed-tools" in frontmatter:
        kwargs["allowed_tools"] = frontmatter["allowed-tools"]

    # Claude Code固有のフロントマターキー（ClaudeAgentOptionsには不要）を除外
    # 参考: https://code.claude.com/docs/en/skills
    exclude_keys = {
        # 基本メタデータ
        "name",
        "description",
        "version",
        # ツール指定（上でマッピング済み）
        "tools",
        "allowed-tools",
        # スキル継承
        "skills",
        "inherits",
        # 呼び出し制御
        "disable-model-invocation",
        "user-invocable",
        # コンテキスト制御
        "context",
    }
    for key, value in frontmatter.items():
        if key not in exclude_keys:
            kwargs[key] = value

    # system_promptはMarkdown本文
    kwargs["system_prompt"] = prompt_str

    return ClaudeAgentOptions(**kwargs)


async def call_agent(
    messages,
    file_path: str | Path | None = None,
    show_options: bool = False,
    show_prompt: bool = False,
    show_response: bool = False,
    show_cost: bool = True,
    show_tools: bool = False,
) -> AgentResult:
    """
    プロンプトを受け取り、Claude Codeに渡して応答を表示し、結果を返す。

    Args:
        messages: 文字列または非同期イテレーター（文字列またはAssistantMessageを返す）
        file_path: エージェント定義ファイルのパス（省略可）
        show_cost: コスト表示
        show_tools: ツール使用表示

    Returns:
        AgentResult: text（応答テキスト）, cost（コスト）, tools_used（使用ツール）

    Usage:
        import asyncio
        from AgentUtil import call_agent

        # 応答を受け取って後続処理に使う
        result = asyncio.run(call_agent("〇〇銘柄を分析して", file_path=".claude/commands/analyst.md"))
        print(result.text)        # 応答テキスト
        print(result.cost)        # コスト（USD）
        print(result.tools_used)  # ["Read", "WebSearch", ...]
    """
    # 文字列ならそのまま使う、非同期イテレーターなら収集
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

    # file_pathが指定されていればパースしてoptions作成
    options = parse_agent_file(file_path) if file_path else None

    # debug_config.yaml から debug_prompt を読み込み
    debug_prompt = ""
    if DEBUG_CONFIG_PATH.exists():
        _cfg = yaml.safe_load(DEBUG_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        debug_prompt = (_cfg.get("debug_prompt") or "").strip()

    # common-prompt.md を読み込み
    common_prompt = ""
    common_prompt_path = Path(__file__).resolve().parent / "common-prompt.md"
    if common_prompt_path.exists() and options:
        common_prompt = common_prompt_path.read_text(encoding="utf-8").strip()

    # エージェント固有のシステムプロンプト（共通プロンプト合成前）を保持
    agent_system_prompt = options.system_prompt if options else ""

    # システムプロンプトの冒頭に挿入: debug_prompt → common_prompt → agent_system_prompt
    if options:
        parts = [p for p in [debug_prompt, common_prompt, agent_system_prompt] if p]
        options.system_prompt = "\n\n".join(parts)

    # AIに渡すプロンプト / オプションをログ出力
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

    # Claude Codeにクエリを投げて応答を表示
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

    # AIの回答をログ出力
    if show_response and result.text:
        print("╔══════════ レス ══════════")
        print(result.text)
        print("╚══════════════════════════")

    return result


if __name__ == "__main__":
    import asyncio

    if len(sys.argv) < 2:
        print("Usage: python utils.py <prompt> [file_path]")
        sys.exit(1)

    prompt = sys.argv[1]
    file_path = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(call_agent(prompt, file_path=file_path))
