"""
Discussion → Planning パイプライン

銘柄を受け取り、Discussion（議論→最終判定）→ Planning（プラン生成）を
自動で連続実行するオーケストレータ。

Usage:
    python discuss_and_plan.py <銘柄> <期間> [モード]

    期間: '短期' / '中期' / '長期'
    モード: '買う' / '売る' (デフォルト: 買う)

例:
    python discuss_and_plan.py NVDA 中期
    python discuss_and_plan.py 楽天 長期 買う
    python discuss_and_plan.py AAPL 短期 売る
"""
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

DISCUSSION_DIR = PROJECT_ROOT / "Discusion" / "src"
DISCUSSION_LOGS = PROJECT_ROOT / "Discusion" / "logs"
PLANNING_DIR = PROJECT_ROOT / "Planning" / "src"

HORIZON_MAP = {
    "短期": "short", "中期": "mid", "長期": "long",
    "short": "short", "mid": "mid", "long": "long",
}
HORIZON_TO_JP = {"short": "短期", "mid": "中期", "long": "長期"}

MODE_MAP = {
    "買う": "buy", "売る": "sell",
    "buy": "buy", "sell": "sell",
}


def _find_venv_python(src_dir: Path) -> str:
    """プロジェクトの venv Python パスを返す。"""
    win = src_dir / ".venv" / "Scripts" / "python.exe"
    unix = src_dir / ".venv" / "bin" / "python"
    if win.exists():
        return str(win)
    if unix.exists():
        return str(unix)
    return "python"


def _find_latest_session_dir(ticker: str) -> Path | None:
    """Discusion/logs/ 配下で最新のセッションディレクトリを返す。

    ディレクトリ名が YYMMDD_HHMM 形式であること、
    かつ指定銘柄の final_judge ファイルが存在することを検証する。
    """
    if not DISCUSSION_LOGS.exists():
        return None

    pattern = re.compile(r"^\d{6}_\d{4}$")
    candidates = sorted(
        (d for d in DISCUSSION_LOGS.iterdir()
         if d.is_dir() and pattern.match(d.name)),
        key=lambda d: d.name,
        reverse=True,
    )

    t = ticker.upper()
    for d in candidates:
        final_judges = list(d.glob(f"{t}_final_judge_*.md"))
        if final_judges:
            return d

    return None


def run_discussion(ticker: str, horizon: str, mode: str) -> int:
    """Discussion を subprocess で実行する。"""
    python = _find_venv_python(DISCUSSION_DIR)
    script = str(DISCUSSION_DIR / "parallel_orchestrator.py")
    cmd = [python, script, ticker, horizon, mode]

    print(f"  コマンド: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(DISCUSSION_DIR))
    return result.returncode


def run_planning(ticker: str, session_dir: str, horizon_jp: str) -> int:
    """Planning を subprocess で実行する。"""
    python = _find_venv_python(PLANNING_DIR)
    script = str(PLANNING_DIR / "plan_orchestrator.py")
    cmd = [python, script, ticker, session_dir, horizon_jp]

    print(f"  コマンド: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PLANNING_DIR))
    return result.returncode


def main():
    if len(sys.argv) < 3:
        print("使い方: python discuss_and_plan.py <銘柄> <期間> [モード]")
        print()
        print("  期間（必須）: '短期' / '中期' / '長期'")
        print("  モード:       '買う' / '売る' (デフォルト: 買う)")
        print()
        print("例:")
        print("  python discuss_and_plan.py NVDA 中期")
        print("  python discuss_and_plan.py 楽天 長期 買う")
        sys.exit(1)

    ticker = sys.argv[1]
    horizon_input = sys.argv[2]

    if horizon_input not in HORIZON_MAP:
        print(f"エラー: 期間 '{horizon_input}' は無効です。'短期' / '中期' / '長期' のいずれかを指定してください。")
        sys.exit(1)

    horizon = HORIZON_MAP[horizon_input]
    horizon_jp = HORIZON_TO_JP[horizon]

    mode_input = sys.argv[3] if len(sys.argv) > 3 else "buy"
    mode = MODE_MAP.get(mode_input, "buy")

    mode_label = "売る" if mode == "sell" else "買う"

    print(f"{'='*60}")
    print(f"=== Discussion → Planning パイプライン ===")
    print(f"=== 銘柄: {ticker} / 期間: {horizon_jp} / モード: {mode_label} ===")
    print(f"{'='*60}")
    print()

    # ── Step 1: Discussion ──
    print(f"{'='*60}")
    print(f"=== Step 1: Discussion（議論→最終判定） ===")
    print(f"{'='*60}")

    exit_code = run_discussion(ticker, horizon, mode)
    if exit_code != 0:
        print()
        print(f"エラー: Discussion が異常終了しました (exit code: {exit_code})")
        print("パイプラインを中断します。")
        sys.exit(exit_code)

    print()
    print("Discussion 完了")
    print()

    # ── Step 2: セッションディレクトリ特定 ──
    session_dir = _find_latest_session_dir(ticker)
    if not session_dir:
        print(f"エラー: Discussion のログディレクトリが見つかりません。")
        print(f"  検索先: {DISCUSSION_LOGS}")
        print(f"  対象銘柄: {ticker.upper()}")
        sys.exit(1)

    print(f"セッションディレクトリ: {session_dir}")
    print()

    # ── Step 3: Planning ──
    print(f"{'='*60}")
    print(f"=== Step 2: Planning（プラン生成） ===")
    print(f"{'='*60}")

    exit_code = run_planning(ticker, str(session_dir), horizon_jp)
    if exit_code != 0:
        print()
        print(f"エラー: Planning が異常終了しました (exit code: {exit_code})")
        sys.exit(exit_code)

    print()

    # ── 完了 ──
    print(f"{'='*60}")
    print(f"=== パイプライン完了 ===")
    print(f"=== {ticker} / {horizon_jp} / {mode_label} ===")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
