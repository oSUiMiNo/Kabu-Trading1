"""
株銘柄評価オーケストレーター

.claude/agents/ 配下のMDファイル（analyst.md, devils-advocate.md）を読み込み、
Claude Agent SDKを使って交互に実行し、筆談ログを作成する。

終了条件:
- 最大ラウンド数 5 に達した
- 末尾EXPORTの stance/confidence が 2 回連続で変わらない（収束）

使い方:
    python 05_stock_orchestrator.py <銘柄コード>

例:
    python 05_stock_orchestrator.py 7203
"""
import sys
from pathlib import Path
import anyio
from datetime import datetime

from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition
from utils import (
    parse_agent_md,
    load_agents_from_dir,
    AgentConfig,
    print_stream,
    build_full_system_prompt,
    extract_latest_export_from_file,
    check_convergence,
    ExportData,
)


# 定数
MAX_ROUNDS = 5  # 最大ラウンド数
CONVERGENCE_COUNT = 2  # 収束判定に必要な連続回数


def agent_config_to_definition(
    config: AgentConfig,
    project_root: str | Path = ".",
) -> AgentDefinition:
    """
    AgentConfigをClaude Agent SDKのAgentDefinitionに変換する。

    CLAUDE.md + スキル内容 + エージェント本文 を結合したシステムプロンプトを使用。

    Args:
        config: parse_agent_mdで取得したAgentConfig
        project_root: プロジェクトルート（CLAUDE.md/スキル検索用）

    Returns:
        AgentDefinition: SDKで使用可能なエージェント定義
    """
    # CLAUDE.md + スキル + エージェント本文を結合
    full_prompt = build_full_system_prompt(
        config,
        project_root=project_root,
        include_claude_md=True,
        include_skills=True,
    )

    return AgentDefinition(
        description=config.description,
        prompt=full_prompt,
        tools=config.tools if config.tools else None,
        model=config.model or "sonnet",
    )


def build_options_from_agents(
    agents: dict[str, AgentConfig],
    project_root: str | Path = ".",
) -> ClaudeAgentOptions:
    """
    複数のAgentConfigからClaudeAgentOptionsを構築する。

    各エージェントのシステムプロンプトには CLAUDE.md + スキル内容 が含まれる。

    Args:
        agents: エージェント名をキーとしたAgentConfig辞書
        project_root: プロジェクトルート

    Returns:
        ClaudeAgentOptions: SDKオプション
    """
    agent_definitions = {
        name: agent_config_to_definition(config, project_root)
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
    project_root: str | Path = ".",
    agents_dir: str | Path = ".claude/agents",
    logs_dir: str | Path = "logs",
):
    """
    株銘柄の考察をAnalyst → Devil's Advocate の順で交互に実行する。

    終了条件:
    - 最大ラウンド数（MAX_ROUNDS=5）に達した
    - stance/confidence が CONVERGENCE_COUNT=2 回連続で変化しなかった

    各エージェント起動時に CLAUDE.md と stock-log-protocol スキルを読み込む。

    Args:
        stock_code: 銘柄コード（例: "7203"）
        project_root: プロジェクトルート
        agents_dir: エージェントMDファイルのディレクトリ
        logs_dir: ログ出力先ディレクトリ
    """
    project_root = Path(project_root)

    # エージェント設定を読み込み
    agents = load_agents_from_dir(agents_dir)

    if "analyst" not in agents:
        raise ValueError("analyst.md が見つかりません")
    if "devils-advocate" not in agents:
        raise ValueError("devils-advocate.md が見つかりません")

    # SDKオプションを構築（CLAUDE.md + スキルを含む）
    options = build_options_from_agents(agents, project_root)

    # ログファイルパス
    logs_path = project_root / logs_dir
    logs_path.mkdir(exist_ok=True)
    log_file = logs_path / f"{stock_code}.md"

    print(f"=== 銘柄 {stock_code} の分析を開始 ===")
    print(f"ログファイル: {log_file}")
    print(f"最大ラウンド数: {MAX_ROUNDS}")
    print(f"収束判定: stance/confidence が {CONVERGENCE_COUNT} 回連続で不変なら終了")
    print("=" * 40)

    # 収束判定用の履歴
    export_history: list[ExportData | None] = []
    convergence_streak = 0  # 連続で変化なしの回数

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"\n{'='*40}")
        print(f"Round {round_num} / {MAX_ROUNDS}")
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

        # === 収束判定 ===
        current_export = extract_latest_export_from_file(log_file)
        export_history.append(current_export)

        if current_export:
            print(f"\n[EXPORT] stance={current_export.stance}, confidence={current_export.confidence}")

        # 前回との比較
        if len(export_history) >= 2:
            previous_export = export_history[-2]
            if check_convergence(current_export, previous_export):
                convergence_streak += 1
                print(f"[収束判定] 変化なし ({convergence_streak}/{CONVERGENCE_COUNT})")

                if convergence_streak >= CONVERGENCE_COUNT:
                    print(f"\n{'='*40}")
                    print(f"収束検出: stance/confidence が {CONVERGENCE_COUNT} 回連続で不変")
                    print(f"Round {round_num} で議論を終了します")
                    print(f"{'='*40}")
                    break
            else:
                convergence_streak = 0
                print(f"[収束判定] 変化あり (リセット)")

    else:
        # forループが break せずに完了した場合
        print(f"\n{'='*40}")
        print(f"最大ラウンド数 {MAX_ROUNDS} に達しました")
        print(f"{'='*40}")

    # 最終結果サマリ
    final_export = extract_latest_export_from_file(log_file)
    print(f"\n=== 分析完了 ===")
    print(f"ログファイル: {log_file}")
    if final_export:
        print(f"最終 stance: {final_export.stance}")
        print(f"最終 confidence: {final_export.confidence}")
    print(f"{'='*40}")


async def main():
    """CLIエントリーポイント"""
    if len(sys.argv) < 2:
        print("使い方: python 05_stock_orchestrator.py <銘柄コード>")
        print("例: python 05_stock_orchestrator.py 7203")
        print()
        print(f"終了条件:")
        print(f"  - 最大 {MAX_ROUNDS} ラウンド")
        print(f"  - stance/confidence が {CONVERGENCE_COUNT} 回連続で不変")
        sys.exit(1)

    stock_code = sys.argv[1]

    # referenceディレクトリから実行する場合、親ディレクトリがプロジェクトルート
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    await orchestrate_stock_analysis(
        stock_code=stock_code,
        project_root=project_root,
    )


if __name__ == "__main__":
    anyio.run(main)
