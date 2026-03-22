"""
最小例1: 基本的なクエリ
Claude Agent SDKを使った最もシンプルな例
"""
import anyio
from claude_agent_sdk import query
from utils import call_agnet


async def main():
    await call_agnet(query(prompt="2 + 2 は何ですか？"))

if __name__ == "__main__":
    anyio.run(main)
