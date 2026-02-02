"""
ファイルパスを受け取り、その内容をClaude Codeにプロンプトとして渡すユーティリティ
"""
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from file_to_stream import parse_agent_file, file_to_stream
from utils import print_stream


async def print_file(
    file_path: str | Path,
    show_cost: bool = True,
    show_tools: bool = False,
    options: ClaudeAgentOptions | None = None,
):
    """
    ファイルパスを受け取り、その内容をClaude Codeにプロンプトとして渡して応答を表示する。

    流れ:
        - フロントマター付きMDファイル（.claude/agents/*.md）の場合:
            1. parse_agent_file(path) → ClaudeAgentOptions（tools/model含む）を取得
            2. print_stream(...) → prompt/tools/modelを反映してClaude Codeに渡す
        - 通常のファイルの場合:
            1. file_to_stream(path) → ファイル内容を非同期イテレーターとして取得
            2. print_stream(...) → 内容をClaude Codeにプロンプトとして渡す

    Args:
        file_path: 読み込むファイルのパス
        show_cost: コスト表示
        show_tools: ツール使用表示
        options: Claude Codeへのオプション（フロントマター付きファイルの場合は上書きされる）

    Usage:
        import asyncio
        from print_file import print_file

        asyncio.run(print_file(".claude/agents/analyst.md"))
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    # フロントマター付きファイルの場合はパースしてtools/modelを反映
    if content.startswith("---"):
        try:
            parsed_options = parse_agent_file(file_path)
            agent_def = next(iter(parsed_options.agents.values()))

            async def prompt_stream():
                yield agent_def.prompt

            await print_stream(
                prompt_stream(),
                show_cost=show_cost,
                show_tools=show_tools,
                options=parsed_options,
            )
            return
        except (ValueError, StopIteration):
            pass

    # 通常のファイル
    await print_stream(
        file_to_stream(file_path),
        show_cost=show_cost,
        show_tools=show_tools,
        options=options,
    )


if __name__ == "__main__":
    import asyncio

    if len(sys.argv) < 2:
        print("Usage: python print_file.py <file_path>")
        sys.exit(1)

    asyncio.run(print_file(sys.argv[1]))