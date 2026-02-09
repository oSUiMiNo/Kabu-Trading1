"""
アクションプラン オーケストレーター

最終判定（Final Judge）完了後に、「次に何をするか」を
具体的な日時つき行動案としてエージェントに生成させ、
logs/ にアクションプランを1ファイル出力する。

他のオーケストレーターと同様、エージェントはテキスト応答のみを返し、
ファイル書き出しはこのオーケストレーター側で行う。
"""
import re
from datetime import datetime
from pathlib import Path

import anyio

from AgentUtil import call_agent, load_debug_config, save_result_log
from discussion_orchestrator import _HORIZON_LABELS
from final_judge_orchestrator import FinalJudgeResult

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
LOGS_DIR = PROJECT_ROOT / "logs"


def get_next_action_plan_num(ticker: str, session_dir: Path | None = None) -> int:
    """既存の action_plan ファイルから次の番号を返す"""
    base = session_dir if session_dir else LOGS_DIR
    pattern = f"{ticker.upper()}_action_plan_*.md"
    existing = list(base.glob(pattern))
    if not existing:
        return 1
    nums = []
    for p in existing:
        m = re.search(r"_action_plan_(\d+)\.md$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def _read_final_judge_log(final_log_path: Path) -> str:
    """最終判定ログを読んで返す"""
    if final_log_path.exists():
        return final_log_path.read_text(encoding="utf-8")
    return ""


def _read_latest_judge_for_plan(ticker: str, set_num: int, session_dir: Path | None = None) -> str:
    """指定レーンの最新 judge ファイルを読んで返す"""
    base = session_dir if session_dir else LOGS_DIR
    t = ticker.upper()
    pattern = f"{t}_set{set_num}_judge_*.md"
    existing = sorted(base.glob(pattern))
    if not existing:
        return ""
    return existing[-1].read_text(encoding="utf-8")


def build_action_plan_prompt(
    ticker: str,
    plan_no: int,
    mode: str,
    horizon: str,
    final_judge_result: FinalJudgeResult,
    agreed_sets: list[int],
    disagreed_sets: list[int],
    session_dir: Path | None = None,
) -> str:
    """action-planner エージェントに渡すプロンプトを組み立てる（ファイル内容インライン埋め込み）"""
    t = ticker.upper()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 保有状況は mode から推定
    position = "保有中" if mode == "sell" else "未保有"

    # 投資期間ラベル
    horizon_label = _HORIZON_LABELS.get(horizon, horizon)

    # 最終判定情報
    verdict = final_judge_result.判定結果
    final_log = final_judge_result.ログパス

    # 最終判定ログの内容をインラインで埋め込む
    final_judge_content = _read_final_judge_log(final_log)

    # 各レーンの judge 内容をインライン埋め込み
    all_sets = sorted(set(agreed_sets) | set(disagreed_sets))
    judge_sections = []
    for sn in all_sets:
        label = "AGREED" if sn in agreed_sets else "DISAGREED"
        judge_content = _read_latest_judge_for_plan(ticker, sn, session_dir)
        section = f"--- レーン{sn} [{label}] judge ここから ---\n"
        if judge_content:
            section += judge_content
        else:
            section += "（judge ファイルなし）"
        section += f"\n--- レーン{sn} judge ここまで ---"
        judge_sections.append(section)
    judge_inline = "\n\n".join(judge_sections)

    return (
        f"銘柄「{t}」のアクションプランを作成してください。\n"
        f"\n"
        f"【前提情報】\n"
        f"  銘柄: {t}\n"
        f"  保有状況: {position}\n"
        f"  投資期間: {horizon_label}\n"
        f"  実行時刻: {now}\n"
        f"  プラン番号: {plan_no}\n"
        f"\n"
        f"【最終判定】\n"
        f"  判定結果: {verdict}\n"
        f"  賛成票: {final_judge_result.賛成票} / 反対票: {final_judge_result.反対票}\n"
        f"\n"
        f"--- 最終判定ログ（{final_log.name}）ここから ---\n"
        f"{final_judge_content if final_judge_content else '（最終判定ログの読み込みに失敗）'}\n"
        f"--- 最終判定ログ ここまで ---\n"
        f"\n"
        f"【各レーンの判定ログ】\n"
        f"{judge_inline}\n"
        f"\n"
        f"上記の内容を踏まえてアクションプランを**テキスト応答として出力**してください。\n"
        f"ファイルへの書き込みは不要です。採番もオーケストレーターが決定済みです（プラン番号: {plan_no}）。"
    )


async def run_action_plan_orchestrator(
    ticker: str,
    mode: str = "buy",
    horizon: str = "mid",
    final_judge_result: FinalJudgeResult | None = None,
    agreed_sets: list[int] | None = None,
    disagreed_sets: list[int] | None = None,
    session_dir: Path | None = None,
) -> None:
    """
    アクションプラン オーケストレーターを実行。

    Args:
        ticker: 銘柄コード
        mode: 議論モード（buy / sell）
        horizon: 投資期間（short / mid / long）
        final_judge_result: 最終判定の結果
        agreed_sets: 一致レーン番号リスト
        disagreed_sets: 不一致レーン番号リスト
    """
    t = ticker.upper()

    if final_judge_result is None:
        print("  ※ 最終判定結果が渡されていないため、アクションプランをスキップします")
        return

    if agreed_sets is None:
        agreed_sets = []
    if disagreed_sets is None:
        disagreed_sets = []

    plan_no = get_next_action_plan_num(ticker, session_dir)

    print(f"=== {t} アクションプラン オーケストレーター ===")
    print(f"  出力: {t}_action_plan_{plan_no}.md")
    print()

    prompt = build_action_plan_prompt(
        ticker, plan_no, mode, horizon,
        final_judge_result, agreed_sets, disagreed_sets,
        session_dir,
    )
    agent_file = AGENTS_DIR / "action-planner.md"

    print(f"[全体] アクションプラン 起動")
    dbg = load_debug_config("action_plan")
    result = await call_agent(
        prompt,
        file_path=str(agent_file),
        show_cost=True,
        show_tools=False,
        **dbg,
    )
    print(f"[全体] アクションプラン 完了")
    if result.cost:
        print(f"  コスト: ${result.cost:.4f}")

    # ログファイルに書き出し
    base = session_dir if session_dir else LOGS_DIR
    plan_path = base / f"{t}_action_plan_{plan_no}.md"
    saved = save_result_log(result, plan_path)
    if saved:
        print(f"  ログ書き出し: {saved.name}")

    print()
