"""
ファイルパスをprint_stream関数に渡せる非同期イテレーターに変換するユーティリティ
"""
from pathlib import Path
from claude_agent_sdk import AssistantMessage, TextBlock


async def file_to_stream(file_path: str | Path):
    """
    ファイルパスを受け取り、その内容をprint_streamに渡せる
    非同期イテレーター形式に変換する。

    Args:
        file_path: 読み込むファイルのパス

    Yields:
        AssistantMessage: ファイル内容をTextBlockでラップしたメッセージ

    Usage:
        from utils import print_stream
        from file_to_stream import file_to_stream

        await print_stream(file_to_stream("path/to/file.txt"))
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    # ファイル内容をAssistantMessageとしてラップ
    message = AssistantMessage(
        model="file",
        content=[TextBlock(text=content)]
    )
    yield message
