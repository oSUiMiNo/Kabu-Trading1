"""
並行オーケストレーター（レーン型アーキテクチャ）

同一銘柄に対して複数のレーンを並行実行する。
各レーンは「議論 → Opinion → Judge」のフローを独立して完結させる。
全レーン完了後、全レーン（AGREED+DISAGREED）でFinal Judgeを実行する。

Before (フェーズ単位バリア同期):
  Phase1(3並列) → 待ち → Phase2 → ...

After (レーン単位独立実行):
  Lane1: 議論→Opinion→Judge ─┐
  Lane2: 議論→Opinion→Judge ─┼→ Final Judge → Action Plan
  Lane3: 議論→Opinion→Judge ─┘  (AGREED+DISAGREEDすべて)
"""
import sys
from pathlib import Path

import anyio

from discussion_orchestrator import LOGS_DIR
from lane_orchestrator import run_lane, LaneResult
from AgentUtil import side_ja
from final_judge_orchestrator import run_final_judge_orchestrator

# レーン番号 → 重視テーマ（num_sets >= 2 の場合に割り当て）
SET_THEMES: dict[int, str] = {
    1: "ファンダメンタル（事業・決算・バリュエーション）",
    2: "カタリスト＆リスク（ニュース・イベント・規制・マクロ）",
    3: "テクニカル＆需給（タイミングとリスク管理）",
}


async def run_parallel(
    ticker: str,
    num_sets: int = 3,
    max_rounds: int = 6,
    initial_prompt: str | None = None,
    opinions_per_set: int = 2,
    mode: str = "buy",
    horizon: str = "mid",
):
    """
    同一銘柄に対して複数レーンを並行実行し、
    全レーン完了後にAGREEDレーンのみでFinal Judgeを実行する。

    フロー:
      1. Nレーンを並行起動（各レーンは議論→Opinion→Judgeを独立実行）
      2. 全レーン完了後、全レーン（AGREED+DISAGREED）でFinal Judgeを実行

    Args:
        ticker: 銘柄コード（例: "NVDA"）
        num_sets: 並行レーン数（デフォルト: 3）
        max_rounds: 各レーンの議論最大ラウンド数
        initial_prompt: 初回Analystへの追加指示（省略可）
        opinions_per_set: 各レーンで生成するOpinion数（デフォルト: 2）
        mode: 議論モード（"buy" = 買う/買わない、"sell" = 売る/売らない）
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    t = ticker.upper()

    from discussion_orchestrator import _HORIZON_LABELS
    mode_label = "売る/売らない" if mode == "sell" else "買う/買わない"
    horizon_label = _HORIZON_LABELS.get(horizon, horizon)
    print(f"{'='*70}")
    print(f"=== {t} {num_sets}レーン ===")
    print(f"=== 議論モード: {mode_label} / 投資期間: {horizon_label} ===")
    print(f"{'='*70}")
    for i in range(1, num_sets + 1):
        theme = SET_THEMES.get(i) if num_sets >= 2 else None
        theme_label = f"  【{theme}】" if theme else ""
        print(f"  レーン{i}: {f'{t}_set{i}.md'}{theme_label}")
    print()

    # 全レーンを並行実行
    lane_results: list[LaneResult] = [None] * num_sets

    async def _run_lane(idx: int, set_num: int):
        """1レーンを実行（エラーは内部でキャッチ済み）"""
        theme = SET_THEMES.get(set_num) if num_sets >= 2 else None
        lane_results[idx] = await run_lane(
            ticker,
            set_num=set_num,
            max_rounds=max_rounds,
            initial_prompt=initial_prompt,
            opinions_per_lane=opinions_per_set,
            mode=mode,
            theme=theme,
            horizon=horizon,
        )

    async with anyio.create_task_group() as tg:
        for i in range(num_sets):
            tg.start_soon(_run_lane, i, i + 1)

    # 全レーン完了後、結果を集約表示
    print()
    print("=" * 70)
    print(f"=== 全{num_sets}レーン完了 — 結果一覧 ===")
    print("=" * 70)

    total_cost = 0.0
    agreed_sets: list[int] = []
    disagreed_sets: list[int] = []
    error_sets: list[int] = []

    for r in lane_results:
        if r is None:
            continue
        total_cost += r.合計コスト

        if r.一致度 == "AGREED":
            agreed_sets.append(r.レーン番号)
            print(f"  レーン{r.レーン番号}: ✓ 一致 ({side_ja(r.支持側)})  ${r.合計コスト:.4f}")
        elif r.一致度 == "DISAGREED":
            disagreed_sets.append(r.レーン番号)
            print(f"  レーン{r.レーン番号}: ✗ 不一致  ${r.合計コスト:.4f}")
        else:
            error_sets.append(r.レーン番号)
            print(f"  レーン{r.レーン番号}: ⚠ エラー  ${r.合計コスト:.4f}")

    print(f"\n  レーン合計コスト: ${total_cost:.4f}")
    print("=" * 70)

    # --- 結果サマリ ---
    if error_sets:
        print(f"\n※エラー※ レーン {', '.join(map(str, error_sets))} でエラーが発生しました")

    if disagreed_sets:
        print(f"\n△不一致 レーン {', '.join(map(str, disagreed_sets))} は意見が不一致")

    # --- 最終判定 ---
    if not agreed_sets and not disagreed_sets:
        print()
        print(" ※終了 全レーンがエラーのため、最終判定は実行しません")
        return

    print()
    if disagreed_sets and agreed_sets:
        print(f">>> 一致レーン {', '.join(map(str, agreed_sets))} + 不一致レーン {', '.join(map(str, disagreed_sets))} で最終判定へ")
    elif agreed_sets:
        print(f">>> 全レーン一致 → 最終判定へ")
    else:
        print(f">>> 全レーン不一致 → 最終判定へ（不一致のみ）")
    print()

    # 各レーンの支持側を集めて投票集計用に渡す
    set_sides = {}
    for r in lane_results:
        if r and r.一致度 == "AGREED" and r.支持側:
            set_sides[r.レーン番号] = r.支持側

    final_result = await run_final_judge_orchestrator(ticker, agreed_sets, mode=mode, disagreed_sets=disagreed_sets, set_sides=set_sides)

    # アクションプラン生成
    from action_plan_orchestrator import run_action_plan_orchestrator
    print()
    print(">>> アクションプラン生成へ")
    print()
    await run_action_plan_orchestrator(
        ticker, mode=mode, horizon=horizon,
        final_judge_result=final_result,
        agreed_sets=agreed_sets, disagreed_sets=disagreed_sets,
    )


if __name__ == "__main__":
    _horizon_map = {"短期": "short", "中期": "mid", "長期": "long",
                    "short": "short", "mid": "mid", "long": "long"}

    if len(sys.argv) < 3 or sys.argv[2] not in _horizon_map:
        print("使い方: python parallel_orchestrator.py <銘柄コード> <投資期間> [モード] [レーン数] [最大ラウンド数] [意見数] [追加指示]")
        print()
        print("  投資期間（必須）: '短期' / '中期' / '長期'")
        print("  モード: '買う' or '売る' (デフォルト: 買う)")
        if len(sys.argv) >= 2 and (len(sys.argv) < 3 or sys.argv[2] not in _horizon_map):
            print()
            print(f"⚠ 投資期間が指定されていません。銘柄の直後に '短期' '中期' '長期' のいずれかを指定してください。")
        sys.exit(1)

    ticker = sys.argv[1]
    horizon = _horizon_map[sys.argv[2]]
    _mode_map = {"買う": "buy", "売る": "sell", "buy": "buy", "sell": "sell"}
    mode = _mode_map.get(sys.argv[3], "buy") if len(sys.argv) > 3 else "buy"
    num_sets = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    max_rounds = int(sys.argv[5]) if len(sys.argv) > 5 else 6
    opinions_per_set = int(sys.argv[6]) if len(sys.argv) > 6 else 2
    initial_prompt = sys.argv[7] if len(sys.argv) > 7 else None

    anyio.run(lambda: run_parallel(ticker, num_sets, max_rounds, initial_prompt, opinions_per_set, mode, horizon))
