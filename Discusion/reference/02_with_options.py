"""
最小例2: オプション付きクエリ
ツール許可やシステムプロンプトを指定する例
"""
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions
from utils import print_stream


async def main():
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Bash"],
        system_prompt="あなたは親切なアシスタントです。日本語で回答してください。",
        max_turns=3,
    )

    await print_stream(
        query(prompt="現在のディレクトリの内容を教えてください", options=options),
        show_tools=True,
    )


if __name__ == "__main__":
    anyio.run(main)
