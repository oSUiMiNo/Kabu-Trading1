"""
YAMLフロントマター付きMarkdownファイルをパースし、ClaudeAgentOptionsとして返すユーティリティ

対象ファイル形式:
    ---
    name: analyst
    description: 株銘柄の考察・分析を行う
    tools:
      - Read
      - Write
    model: sonnet
    ---

    # プロンプト本文
    あなたは...
"""
from pathlib import Path

import yaml
from claude_agent_sdk import ClaudeAgentOptions, AgentDefinition


def parse_agent_file(file_path: str | Path) -> ClaudeAgentOptions:
    """
    YAMLフロントマター付きMarkdownファイルをパースしてClaudeAgentOptionsを返す。

    Args:
        file_path: 読み込むファイルのパス（.claude/agents/*.md 形式）

    Returns:
        ClaudeAgentOptions: agents={name: AgentDefinition(...)} を含むオプション

    Raises:
        ValueError: フロントマターが見つからない場合

    Usage:
        from file_to_stream import parse_agent_file

        options = parse_agent_file(".claude/agents/analyst.md")
        # options.agents["analyst"].description → "株銘柄の考察・分析を行う"
        # options.agents["analyst"].tools → ["Read", "Write"]
        # options.agents["analyst"].prompt → "# Analyst（考察サブエージェント）\n..."
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    # フロントマターを分離（--- で囲まれた部分）
    if not content.startswith("---"):
        raise ValueError(f"フロントマターが見つかりません: {file_path}")

    # 2つ目の --- を探す
    end_index = content.find("---", 3)
    if end_index == -1:
        raise ValueError(f"フロントマターの終端が見つかりません: {file_path}")

    frontmatter_str = content[3:end_index].strip()
    prompt_str = content[end_index + 3 :].strip()

    # YAMLをパース
    frontmatter = yaml.safe_load(frontmatter_str) or {}

    name = frontmatter.get("name", path.stem)

    agent_def = AgentDefinition(
        description=frontmatter.get("description", ""),
        prompt=prompt_str,
        tools=frontmatter.get("tools", []),
        model=frontmatter.get("model", "sonnet"),
    )

    return ClaudeAgentOptions(agents={name: agent_def})


async def file_to_stream(file_path: str | Path):
    """
    ファイルパスを受け取り、その内容を非同期イテレーターとして返す。

    フロントマター付きMDファイルの場合はプロンプト部分のみをyieldする。
    それ以外のファイルはそのまま内容をyieldする。

    Args:
        file_path: 読み込むファイルのパス

    Yields:
        str: ファイル内容（またはプロンプト部分）

    Usage:
        from file_to_stream import file_to_stream
        from utils import print_stream

        await print_stream(file_to_stream("path/to/file.txt"))
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    # フロントマター付きファイルの場合はプロンプト部分のみ返す
    if content.startswith("---"):
        try:
            options = parse_agent_file(file_path)
            # agents辞書から最初のAgentDefinitionのpromptを取得
            agent_def = next(iter(options.agents.values()))
            yield agent_def.prompt
            return
        except (ValueError, StopIteration):
            pass

    yield content
