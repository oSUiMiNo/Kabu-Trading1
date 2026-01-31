"""
株銘柄評価オーケストレーター

.claude/agents/ 配下のMDファイル（analyst.md, devils-advocate.md）を読み込み、
Claude Agent SDKを使って交互に実行し、筆談ログを作成する。

使い方:
    python 05_stock_orchestrator.py <銘柄コード> [ラウンド数]

例:
    python 05_stock_orchestrator.py 7203 2
"""
import sys
from pathlib import Path
import anyio
from datetime import datetime

from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition
from utils import parse_agent_md, load_agents_from_dir, AgentConfig, print_stream


def agent_config_to_definition(config: AgentConfig) -> AgentDefinition:
    """
    AgentConfigをClaude Agent SDKのAgentDefinitionに変換する。

    Args:
        config: parse_agent_mdで取得したAgentConfig

    Returns:
        AgentDefinition: SDKで使用可能なエージェント定義
    """
    return AgentDefinition(
        description=config.description,
        prompt=config.system_prompt,
        tools=config.tools if config.tools else None,
        model=config.model or "sonnet",
    )


def build_options_from_agents(agents: dict[str, AgentConfig]) -> ClaudeAgentOptions:
    """
    複数のAgentConfigからClaudeAgentOptionsを構築する。

    Args:
        agents: エージェント名をキーとしたAgentConfig辞書

    Returns:
        ClaudeAgentOptions: SDKオプション
    """
    agent_definitions = {
        name: agent_config_to_definition(config)
        for name, config in agents.items()
    }

    return ClaudeAgentOptions(
        agents=agent_definitions,
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch"],
    )


async def run_agent(
    agent_name: str,
    prompt: str,
    options: ClaudeAgentOptions,
    show_output: bool = True,
) -> str:
    """
    指定したエージェントを実行し、結果テキストを返す。

    Args:
        agent_name: 実行するエージェント名
        prompt: エージェントへの指示
        options: SDKオプション
        show_output: 実行中の出力を表示するか

    Returns:
        str: エージェントの応答テキスト
    """
    full_prompt = f"{agent_name} エージェントを使って、以下を実行してください:\n\n{prompt}"

    result_texts = []
    async for msg in query(prompt=full_prompt, options=options):
        from utils import extract_text
        text = extract_text(msg)
        if text:
            result_texts.append(text)
            if show_output:
                print(text)

    return "\n".join(result_texts)


async def orchestrate_stock_analysis(
    stock_code: str,
    agents_dir: str | Path = ".claude/agents",
    logs_dir: str | Path = "logs",
    rounds: int = 2,
):
    """
    株銘柄の考察をAnalyst → Devil's Advocate の順で交互に実行する。

    Args:
        stock_code: 銘柄コード（例: "7203"）
        agents_dir: エージェントMDファイルのディレクトリ
        logs_dir: ログ出力先ディレクトリ
        rounds: 議論のラウンド数（1ラウンド = Analyst + Devil's Advocate）
    """
    # エージェント設定を読み込み
    agents = load_agents_from_dir(agents_dir)

    if "analyst" not in agents:
        raise ValueError("analyst.md が見つかりません")
    if "devils-advocate" not in agents:
        raise ValueError("devils-advocate.md が見つかりません")

    # SDKオプションを構築
    options = build_options_from_agents(agents)

    # ログファイルパス
    logs_path = Path(logs_dir)
    logs_path.mkdir(exist_ok=True)
    log_file = logs_path / f"{stock_code}.md"

    print(f"=== 銘柄 {stock_code} の分析を開始 ===")
    print(f"ログファイル: {log_file}")
    print(f"ラウンド数: {rounds}")
    print("=" * 40)

    for round_num in range(1, rounds + 1):
        print(f"\n{'='*40}")
        print(f"Round {round_num}")
        print(f"{'='*40}")

        # === Analyst フェーズ ===
        print(f"\n--- Round {round_num}: Analyst ---\n")

        if round_num == 1:
            analyst_prompt = f"""
銘柄コード {stock_code} の考察を開始してください。

1. ログファイル {log_file} が存在するか確認し、なければ初期化
2. Sources（S#）を収集し、Facts（F#）を整理
3. Claims（C#）を組み立て、Round {round_num} として追記
4. 暫定結論とEXPORTを出力
"""
        else:
            analyst_prompt = f"""
銘柄コード {stock_code} の考察を継続してください。

1. ログファイル {log_file} を読み、前回のDevil's Advocateの指摘を確認
2. 指摘に対して必要であれば追加調査し、主張を修正または補強
3. Round {round_num} - Analyst として追記
4. 暫定結論とEXPORTを出力
"""

        await run_agent("analyst", analyst_prompt, options)

        # === Devil's Advocate フェーズ ===
        print(f"\n--- Round {round_num}: Devil's Advocate ---\n")

        da_prompt = f"""
銘柄コード {stock_code} のAnalystの主張を検証してください。

1. ログファイル {log_file} を読み、直前のAnalystのRoundを確認
2. C#がF#[S#]で支えられているか検査
3. 重要度上位から最大2件の反論を Round {round_num} - Devil's Advocate として追記
4. 鮮度トリガー該当時は retrieved_at を優先チェック
5. 暫定結論とEXPORTを出力
"""

        await run_agent("devils-advocate", da_prompt, options)

    print(f"\n{'='*40}")
    print(f"分析完了: {log_file}")
    print(f"{'='*40}")


async def main():
    """CLIエントリーポイント"""
    if len(sys.argv) < 2:
        print("使い方: python 05_stock_orchestrator.py <銘柄コード> [ラウンド数]")
        print("例: python 05_stock_orchestrator.py 7203 2")
        sys.exit(1)

    stock_code = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    await orchestrate_stock_analysis(
        stock_code=stock_code,
        rounds=rounds,
    )


if __name__ == "__main__":
    anyio.run(main)
