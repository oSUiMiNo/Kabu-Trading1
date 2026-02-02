"""
ファイルパスを受け取り、その内容をClaude Codeにプロンプトとして渡すユーティリティ
"""
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions

from file_to_stream import file_to_stream
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
        1. file_to_stream(path) → ファイル内容を非同期イテレーターとして取得
        2. print_stream(...) → 内容をClaude Codeにプロンプトとして渡し、応答を表示

    Args:
        file_path: 読み込むファイルのパス
        show_cost: コスト表示
        show_tools: ツール使用表示
        options: Claude Codeへのオプション（allowed_tools, system_prompt等）

    Usage:
        import asyncio
        from print_file import print_file

        asyncio.run(print_file("prompt.txt"))
    """
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