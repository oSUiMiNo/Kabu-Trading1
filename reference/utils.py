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


def load_skill_content(skill_name: str, project_root: str | Path = ".") -> str | None:
    """
    スキル名からSKILL.mdの本文（フロントマター除く）を読み込む。

    検索順序:
    1. .claude/skills/<skill_name>/SKILL.md
    2. ~/.claude/skills/<skill_name>/SKILL.md

    Args:
        skill_name: スキル名（ディレクトリ名）
        project_root: プロジェクトルート

    Returns:
        str | None: スキルの本文。見つからなければNone
    """
    project_path = Path(project_root)
    home_path = Path.home()

    # 検索パスリスト
    search_paths = [
        project_path / ".claude" / "skills" / skill_name / "SKILL.md",
        home_path / ".claude" / "skills" / skill_name / "SKILL.md",
    ]

    for skill_path in search_paths:
        if skill_path.exists():
            content = skill_path.read_text(encoding="utf-8")
            _, body = _split_frontmatter(content)
            return body.strip()

    return None


def load_claude_md(project_root: str | Path = ".") -> str | None:
    """
    CLAUDE.mdを読み込む。

    検索順序:
    1. .claude/CLAUDE.md
    2. CLAUDE.md（プロジェクトルート）
    3. ~/.claude/CLAUDE.md

    Args:
        project_root: プロジェクトルート

    Returns:
        str | None: CLAUDE.mdの内容。見つからなければNone
    """
    project_path = Path(project_root)
    home_path = Path.home()

    search_paths = [
        project_path / ".claude" / "CLAUDE.md",
        project_path / "CLAUDE.md",
        home_path / ".claude" / "CLAUDE.md",
    ]

    for claude_md_path in search_paths:
        if claude_md_path.exists():
            return claude_md_path.read_text(encoding="utf-8")

    return None


def build_full_system_prompt(
    config: AgentConfig,
    project_root: str | Path = ".",
    include_claude_md: bool = True,
    include_skills: bool = True,
) -> str:
    """
    AgentConfigから完全なシステムプロンプトを構築する。

    CLAUDE.md + スキル内容 + エージェント本文 を結合して返す。

    Args:
        config: パース済みのAgentConfig
        project_root: プロジェクトルート
        include_claude_md: CLAUDE.mdを含めるか
        include_skills: スキル内容を含めるか

    Returns:
        str: 結合されたシステムプロンプト
    """
    parts = []

    # 1. CLAUDE.md（共通ルール）
    if include_claude_md:
        claude_md = load_claude_md(project_root)
        if claude_md:
            parts.append("# プロジェクト共通ルール（CLAUDE.md）\n")
            parts.append(claude_md)
            parts.append("\n---\n")

    # 2. スキル内容
    if include_skills and config.skills:
        for skill_name in config.skills:
            skill_content = load_skill_content(skill_name, project_root)
            if skill_content:
                parts.append(f"# スキル: {skill_name}\n")
                parts.append(skill_content)
                parts.append("\n---\n")

    # 3. エージェント本文
    parts.append(config.system_prompt)

    return "\n".join(parts)


@dataclass
class ExportData:
    """EXPORTブロックのパース結果"""
    stance: str | None = None
    confidence: str | None = None
    as_of: str | None = None
    raw: dict = field(default_factory=dict)


def extract_latest_export(log_content: str) -> ExportData | None:
    """
    ログファイルから最後のEXPORTブロックを抽出してパースする。

    Args:
        log_content: ログファイルの全内容

    Returns:
        ExportData | None: パース結果。EXPORTが見つからなければNone
    """
    # ### EXPORT（yaml） または ### EXPORT のブロックを探す
    pattern = r"###\s+EXPORT[^\n]*\n(.*?)(?=\n##|\n---|\Z)"
    matches = list(re.finditer(pattern, log_content, re.DOTALL | re.IGNORECASE))

    if not matches:
        return None

    # 最後のEXPORTを取得
    last_match = matches[-1]
    yaml_content = last_match.group(1).strip()

    # YAMLブロック内のコードフェンスを除去
    yaml_content = re.sub(r"^```ya?ml?\s*\n?", "", yaml_content)
    yaml_content = re.sub(r"\n?```\s*$", "", yaml_content)

    try:
        data = yaml.safe_load(yaml_content)
        if data is None:
            data = {}
    except yaml.YAMLError:
        # YAMLパース失敗時は行単位で簡易パース
        data = _simple_yaml_parse(yaml_content)

    return ExportData(
        stance=data.get("stance"),
        confidence=data.get("confidence"),
        as_of=data.get("as_of"),
        raw=data,
    )


def _simple_yaml_parse(content: str) -> dict:
    """
    簡易的なYAMLパーサー（key: value形式のみ対応）
    """
    result = {}
    for line in content.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # リスト形式の値は文字列として保持
            if value.startswith("[") and value.endswith("]"):
                result[key] = value
            else:
                result[key] = value
    return result


def extract_latest_export_from_file(filepath: str | Path) -> ExportData | None:
    """
    ログファイルパスから最後のEXPORTを抽出する。

    Args:
        filepath: ログファイルのパス

    Returns:
        ExportData | None: パース結果。ファイルが存在しないかEXPORTが見つからなければNone
    """
    path = Path(filepath)
    if not path.exists():
        return None

    content = path.read_text(encoding="utf-8")
    return extract_latest_export(content)


def check_convergence(
    current: ExportData | None,
    previous: ExportData | None,
) -> bool:
    """
    stance と confidence が変化したかチェックする。

    Args:
        current: 現在のEXPORT
        previous: 前回のEXPORT

    Returns:
        bool: 変化がなければTrue（収束）、変化があればFalse
    """
    if current is None or previous is None:
        return False

    stance_same = current.stance == previous.stance
    confidence_same = current.confidence == previous.confidence

    return stance_same and confidence_same


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
