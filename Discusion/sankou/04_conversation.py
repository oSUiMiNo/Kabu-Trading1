"""
最小例4: 対話型セッション
ClaudeSDKClientを使った複数ターンの会話
"""
import anyio
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from utils import print_stream


async def main():
    options = ClaudeAgentOptions(
        allowed_tools=["Read", "Bash"],
        system_prompt="あなたは親切なアシスタントです。日本語で回答してください。",
    )

    async with ClaudeSDKClient(options=options) as client:
        print("--- 1ターン目 ---")
        await client.query("このディレクトリにあるPythonファイルを教えてください")
        await print_stream(client.receive_response(), show_tools=True)

        print("\n--- 2ターン目 ---")
        await client.query("その中で一番シンプルなファイルの内容を見せてください")
        await print_stream(client.receive_response(), show_tools=True)


if __name__ == "__main__":
    anyio.run(main)
