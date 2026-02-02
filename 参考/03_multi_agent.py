"""
最小例3: マルチエージェント・オーケストレーション
複数の専門エージェントを定義して使い分ける例
"""
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition
from utils import print_stream


async def main():
    options = ClaudeAgentOptions(
        description="マルチエージェント・オーケストレーション例",
        allowed_tools=["Read", "Write", "Grep", "Bash"],
        agents={
            "reviewer": AgentDefinition(
                description="コードをレビューする専門家",
                prompt="あなたはコードレビューの専門家です。"
                       "バグ、パフォーマンス問題、セキュリティ脆弱性を分析し、"
                       "建設的なフィードバックを日本語で提供してください。",
                tools=["Read", "Grep"],
                model="sonnet",
            ),
            "doc-writer": AgentDefinition(
                description="ドキュメントを作成する専門家",
                prompt="あなたは技術文書の専門家です。"
                       "明確で分かりやすいドキュメントを日本語で作成してください。",
                tools=["Read", "Write"],
                model="sonnet",
            ),
            "tester": AgentDefinition(
                description="テストを作成・実行する専門家",
                prompt="あなたはテストの専門家です。"
                       "包括的なテストを作成し、コード品質を確保してください。",
                tools=["Read", "Write", "Bash"],
                model="sonnet",
            ),
        },
    )

    print("=== reviewer エージェントを使用 ===\n")
    await print_stream(
        query(
            prompt="reviewer エージェントを使って、このプロジェクトのPythonファイルをレビューしてください",
            options=options,
        )
    )


if __name__ == "__main__":
    anyio.run(main)
