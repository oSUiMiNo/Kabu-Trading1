"""
Claude Agent SDK 共通ユーティリティ
"""
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
    ClaudeAgentOptions,
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


async def print_stream(
    messages,
    show_cost: bool = True,
    show_tools: bool = False,
    options: ClaudeAgentOptions | None = None,
):
    """
    非同期イテレーターからプロンプトを受け取り、Claude Codeに渡して応答を表示する。

    Args:
        messages: 非同期イテレーター（文字列またはAssistantMessageを返す）
        show_cost: コスト表示
        show_tools: ツール使用表示
        options: Claude Codeへのオプション（allowed_tools, system_prompt等）
    """
    # 非同期イテレーターから内容を収集
    prompt_parts = []
    async for msg in messages:
        if isinstance(msg, str):
            prompt_parts.append(msg)
        elif isinstance(msg, AssistantMessage):
            text = extract_text(msg)
            if text:
                prompt_parts.append(text)

    prompt = "\n".join(prompt_parts)

    # Claude Codeにクエリを投げて応答を表示
    async for response in query(prompt=prompt, options=options):
        text = extract_text(response)
        if text:
            print(text)

        if show_tools:
            for tool in extract_tool_use(response):
                print(f"[ツール: {tool}]")

        if show_cost:
            cost = extract_cost(response)
            if cost:
                print(f"\n(コスト: ${cost:.4f})")
