"""
Claude Agent SDK 共通ユーティリティ
"""
from pathlib import Path
from dataclasses import dataclass, field
import re
import yaml

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)


@dataclass
class AgentConfig:
    """サブエージェントMDファイルのパース結果を保持するデータクラス"""
    name: str
    description: str
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    model: str | None = None
    system_prompt: str = ""
    raw_frontmatter: dict = field(default_factory=dict)
    source_path: Path | None = None


def parse_agent_md(filepath: str | Path) -> AgentConfig:
    """
    サブエージェントMDファイルをパースしてAgentConfigを返す。

    MDファイルは以下の構造を想定:
    ---
    name: agent-name
    description: 説明文
    tools:
      - Read
      - Write
    skills:
      - some-skill
    model: sonnet  # optional
    ---

    # 本文（システムプロンプト）
    ...

    Args:
        filepath: MDファイルのパス

    Returns:
        AgentConfig: パース結果

    Raises:
        FileNotFoundError: ファイルが存在しない場合
        ValueError: フロントマターのパースに失敗した場合
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {filepath}")

    content = path.read_text(encoding="utf-8")

    # フロントマターと本文を分離
    frontmatter, body = _split_frontmatter(content)

    # フロントマターをパース
    if frontmatter:
        try:
            meta = yaml.safe_load(frontmatter)
            if meta is None:
                meta = {}
        except yaml.YAMLError as e:
            raise ValueError(f"フロントマターのパースに失敗: {e}")
    else:
        meta = {}

    return AgentConfig(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        tools=meta.get("tools", []),
        skills=meta.get("skills", []),
        model=meta.get("model"),
        system_prompt=body.strip(),
        raw_frontmatter=meta,
        source_path=path,
    )


def _split_frontmatter(content: str) -> tuple[str | None, str]:
    """
    Markdown文字列からフロントマター（YAML）と本文を分離する。

    Args:
        content: MDファイルの全内容

    Returns:
        tuple[frontmatter, body]: フロントマター文字列（なければNone）と本文
    """
    # フロントマターは --- で囲まれた部分
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, content, re.DOTALL)

    if match:
        return match.group(1), match.group(2)
    else:
        # フロントマターがない場合は全体が本文
        return None, content


def load_agents_from_dir(directory: str | Path) -> dict[str, AgentConfig]:
    """
    ディレクトリ内の全MDファイルをパースしてエージェント設定を返す。

    Args:
        directory: .claude/agents/ などのディレクトリパス

    Returns:
        dict[str, AgentConfig]: エージェント名をキーとした設定辞書
    """
    dir_path = Path(directory)
    agents = {}

    if not dir_path.exists():
        return agents

    for md_file in dir_path.glob("*.md"):
        try:
            config = parse_agent_md(md_file)
            agents[config.name] = config
        except (ValueError, FileNotFoundError) as e:
            print(f"警告: {md_file} のパースをスキップ: {e}")

    return agents


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


async def print_stream(messages, show_cost=True, show_tools=False):
    """ストリームからメッセージを表示する汎用関数"""
    async for msg in messages:
        text = extract_text(msg)
        if text:
            print(text)

        if show_tools:
            for tool in extract_tool_use(msg):
                print(f"[ツール: {tool}]")

        if show_cost:
            cost = extract_cost(msg)
            if cost:
                print(f"\n(コスト: ${cost:.4f})")
