"""
株銘柄評価オーケストレーター

analyst と devils-advocate を交互に呼び出し、
筆談ログ（logs/{TICKER}.md）上で議論させる。
オーケストレーター自体はLLMを使わず、プログラムだけで制御する。
"""
import sys
import re
from pathlib import Path

import anyio

from AgentUtil import call_agent, AgentResult, load_debug_config, save_result_log

MAX_AGENT_RETRIES = 3

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
LOGS_DIR = PROJECT_ROOT / "logs"


def get_log_path(ticker: str) -> Path:
    return LOGS_DIR / f"{ticker.upper()}.md"


def get_last_round(log_path: Path) -> int:
    """ログファイルから最後のラウンド番号を取得"""
    if not log_path.exists():
        return 0
    content = log_path.read_text(encoding="utf-8")
    rounds = re.findall(r"## Round (\d+)", content)
    return int(rounds[-1]) if rounds else 0


def get_last_export(log_path: Path) -> dict | None:
    """ログファイルから最後のEXPORTブロックを簡易パース"""
    if not log_path.exists():
        return None
    content = log_path.read_text(encoding="utf-8")

    # 最後の```yaml ... ``` ブロックを探す
    exports = re.findall(r"```yaml\n(.*?)```", content, re.DOTALL)
    if not exports:
        return None

    result = {}
    for line in exports[-1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def check_convergence(export: dict | None, prev_export: dict | None) -> bool:
    """2つのEXPORT間でstanceとconfidenceが一致していれば収束と判定"""
    if not export or not prev_export:
        return False
    return (
        export.get("stance") == prev_export.get("stance")
        and export.get("confidence") == prev_export.get("confidence")
    )


def _mode_directive(mode: str) -> str:
    """議論のスタンス指示行を返す（5択統一）"""
    if mode in ("sell", "add"):
        holding = "※ この銘柄は現在保有中です。\n"
    else:
        holding = "※ この銘柄は現在未保有です。\n"
    return (
        "【アクション判定】\n"
        f"{holding}"
        "この銘柄に対して最適なアクションを、以下の5つから1つ選んでスタンスとしてください。\n"
        "- 買う（BUY）: 新規に購入する\n"
        "- 売る（SELL）: 保有分を全て売却する\n"
        "- 買い増す（ADD）: 保有分に追加購入する\n"
        "- 売り減らす（REDUCE）: 保有分の一部を売却する\n"
        "- 現状維持（HOLD）: 何もしない\n\n"
    )


_HORIZON_LABELS: dict[str, str] = {
    "short": "短期（数日〜数週間）",
    "mid": "中期（数週間〜数ヶ月）",
    "long": "長期（半年以上）",
}


def _horizon_directive(horizon: str) -> str:
    """投資期間をプロンプト先頭に挿入する指示行を返す"""
    label = _HORIZON_LABELS.get(horizon, horizon)
    return f"【投資期間: {label}】この期間を前提にリスク・リターンを評価してください。\n\n"


def _theme_directive(theme: str | None) -> str:
    """重視テーマをプロンプトに挿入する指示行を返す"""
    if not theme:
        return ""
    return (
        f"【重視テーマ: {theme}】\n"
        f"このレーンでは上記テーマを特に重視して分析してください。\n"
        f"ただし他の領域も必要に応じて考察対象に含めてください。\n\n"
    )


def _ticker_label(ticker: str, display_name: str = "") -> str:
    """ティッカーに display_name があれば併記する（例: 'U (Unity)'）。"""
    t = ticker.upper()
    return f"{t} ({display_name})" if display_name else t


def build_prompt(ticker: str, role: str, round_num: int, log_path: Path, mode: str = "buy", theme: str | None = None, horizon: str = "mid", display_name: str = "") -> str:
    """各エージェントに渡すプロンプトを組み立てる"""
    log_abs = log_path.as_posix()
    directive = _mode_directive(mode)
    horizon_dir = _horizon_directive(horizon)
    theme_dir = _theme_directive(theme)
    label = _ticker_label(ticker, display_name)

    if role == "analyst":
        if round_num == 1:
            return (
                f"{directive}"
                f"{horizon_dir}"
                f"{theme_dir}"
                f"銘柄「{label}」の初回分析を行ってください。\n"
                f"ログファイル（参照用）: {log_abs}\n"
                f"ログの初期構造（メタ情報、Sources表、Facts、Claims）と Round {round_num} の内容を含めて、\n"
                f"**テキスト応答として出力**してください。ファイルへの書き込みは不要です。"
            )
        else:
            return (
                f"{directive}"
                f"{horizon_dir}"
                f"{theme_dir}"
                f"銘柄「{label}」の分析を続けてください。\n"
                f"ログファイル（参照用）: {log_abs}\n"
                f"前回のDevil's Advocateが異なるスタンスから出した反論Claimsを読み、\n"
                f"Round {round_num} の内容を**テキスト応答として出力**してください。ファイルへの書き込みは不要です。"
            )
    else:  # devils-advocate
        return (
            f"{directive}"
            f"{horizon_dir}"
            f"{theme_dir}"
            f"銘柄「{label}」のログを読み、最新のAnalystのstanceとは異なるスタンスを取ってください。\n"
            f"ログファイル（参照用）: {log_abs}\n"
            f"Analystが選ばなかった残りのスタンスの中から最も妥当なものを選び、その立場から反論Claims（C#）を組み立てて、\n"
            f"Round {round_num} の内容を**テキスト応答として出力**してください。ファイルへの書き込みは不要です。"
        )


async def run_discussion(
    ticker: str,
    max_rounds: int = 4,
    initial_prompt: str | None = None,
    log_path: Path | None = None,
    mode: str = "buy",
    theme: str | None = None,
    horizon: str = "mid",
    display_name: str = "",
):
    """
    オーケストレーターのメインループ。

    Args:
        ticker: 銘柄コード（例: "NVDA"）
        max_rounds: 最大ラウンド数（Analyst + Devil's Advocate で2ラウンド = 1サイクル）
        initial_prompt: 初回Analystへの追加指示（省略可）
        log_path: ログファイルパス（省略時は logs/{TICKER}.md）
        mode: 保有状況（"buy" = 未保有、"sell" / "add" = 保有中）
    """
    if log_path is None:
        log_path = get_log_path(ticker)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 初回ログファイル作成（エージェントがReadできるように空ファイルを用意）
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")

    analyst_file = AGENTS_DIR / "analyst.md"
    devils_file = AGENTS_DIR / "devils-advocate.md"

    start_round = get_last_round(log_path) + 1
    prev_export = get_last_export(log_path)

    # ログパスからレーン番号を抽出（表示用）
    _lane_match = re.search(r"_set(\d+)\.md$", log_path.name)
    lane_label = f"レーン{_lane_match.group(1)}" if _lane_match else log_path.stem

    for i in range(max_rounds):
        round_num = start_round + i

        # 奇数ラウンド=Analyst、偶数ラウンド=Devil's Advocate
        if round_num % 2 == 1:
            role = "analyst"
            agent_file = analyst_file
            label = "Analyst"
        else:
            role = "devils-advocate"
            agent_file = devils_file
            label = "Devil's Advocate"

        print(f"---{lane_label} ラウンド{round_num}: {label} {'-' * 30}")

        # プロンプト組み立て
        prompt = build_prompt(ticker, role, round_num, log_path, mode, theme, horizon, display_name)
        if initial_prompt and round_num == start_round and role == "analyst":
            prompt = f"{initial_prompt}\n\n{prompt}"

        # エージェント呼び出し（リトライ付き）
        dbg = load_debug_config("discussion")
        show_cost = dbg.pop("show_cost", False)

        result = AgentResult()
        for attempt in range(1, MAX_AGENT_RETRIES + 1):
            if attempt > 1:
                print(f"---{lane_label} ラウンド{round_num} リトライ {attempt}/{MAX_AGENT_RETRIES}")
            try:
                result = await call_agent(
                    prompt,
                    file_path=str(agent_file),
                    show_cost=show_cost,
                    show_tools=False,
                    **dbg,
                )
                if result and result.text:
                    break
                print(f"---{lane_label} ラウンド{round_num} 警告: 応答なし")
            except Exception as e:
                print(f"---{lane_label} ラウンド{round_num} エラー: {e}")

        print(f"---{lane_label} ラウンド{round_num} 完了 {'-' * 30}")
        if show_cost and result.cost:
            print(f"コスト: ${result.cost:.4f}")

        # オーケストレーター側でログに追記
        saved = save_result_log(result, log_path, append=True)
        

        # 収束チェック
        current_export = get_last_export(log_path)
        if check_convergence(current_export, prev_export):
            print(f"=== 収束検出: 立場/確信度 が前ラウンドと一致 ===")
            print(f"立場: {current_export.get('stance')}")
            print(f"確信度: {current_export.get('confidence')}")
            break

        prev_export = current_export

    final_export = get_last_export(log_path)
    if final_export:
        print(f"最終立場: {final_export.get('stance', 'N/A')}")
        print(f"最終確信度: {final_export.get('confidence', 'N/A')}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python discussion_orchestrator.py <銘柄コード> [モード] [最大ラウンド数] [追加指示]")
        print("  モード: '買う'(未保有) / '売る'(保有中) / '買い増す'(保有中) (デフォルト: 買う)")
        print("例: python discussion_orchestrator.py NVDA 買う 4 '特にAI市場の競合状況に注目して'")
        print("例: python discussion_orchestrator.py NVDA 売る 4")
        print("例: python discussion_orchestrator.py NVDA 買い増す 4")
        sys.exit(1)

    ticker = sys.argv[1]
    _mode_map = {"買う": "buy", "売る": "sell", "買い増す": "add", "buy": "buy", "sell": "sell", "add": "add"}
    mode = _mode_map.get(sys.argv[2], "buy") if len(sys.argv) > 2 else "buy"
    max_rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    initial_prompt = sys.argv[4] if len(sys.argv) > 4 else None

    anyio.run(lambda: run_discussion(ticker, max_rounds, initial_prompt, mode=mode))
