"""
ファイルパスを受け取り、その内容を非同期イテレーターとして返すユーティリティ
"""
from pathlib import Path


async def file_to_stream(file_path: str | Path):
    """
    ファイルパスを受け取り、その内容を非同期イテレーターとして返す。

    Args:
        file_path: 読み込むファイルのパス

    Yields:
        str: ファイル内容

    Usage:
        from file_to_stream import file_to_stream
        from utils import print_stream

        await print_stream(file_to_stream("path/to/file.txt"))
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")
    yield content
