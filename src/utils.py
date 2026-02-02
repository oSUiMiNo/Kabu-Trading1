"""
Claude Agent SDK 共通ユーティリティ
"""
import sys
from pathlib import Path

import yaml
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
    ClaudeAgentOptions,
)


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
        options = parse_agent_file(".claude/agents/analyst.md")
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
    show_cost: bool = True,
    show_tools: bool = False,
):
    """
    プロンプトを受け取り、Claude Codeに渡して応答を表示する。

    Args:
        messages: 文字列または非同期イテレーター（文字列またはAssistantMessageを返す）
        file_path: エージェント定義ファイルのパス（省略可）
        show_cost: コスト表示
        show_tools: ツール使用表示

    Usage:
        import asyncio
        from utils import call_agent

        # ファイル指定あり（エージェント定義からsystem_prompt/tools/modelを取得）
        asyncio.run(call_agent("〇〇銘柄を分析して", file_path=".claude/agents/analyst.md"))

        # ファイル指定なし（プロンプトのみ）
        asyncio.run(call_agent("こんにちは"))
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

    # Claude Codeにクエリを投げて応答を表示
    async for response in query(prompt=prompt, options=options):
        text = extract_text(response)
        if text:
            print(text)

        if show_tools:
            for tool in extract_tool_use(response):
                print(f"[ツール: {tool}]")

        if show_cost:
            cost = extract_cost(response)
            if cost:
                print(f"\n(コスト: ${cost:.4f})")


if __name__ == "__main__":
    import asyncio

    if len(sys.argv) < 2:
        print("Usage: python utils.py <prompt> [file_path]")
        sys.exit(1)

    prompt = sys.argv[1]
    file_path = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(call_agent(prompt, file_path=file_path))
