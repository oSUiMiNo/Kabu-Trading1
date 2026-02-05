"""
Claude Agent SDK 共通ユーティリティ
"""
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)


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


async def call_agnet(messages, show_cost=True, show_tools=False):
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
