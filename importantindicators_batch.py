"""
ImportantIndicators バッチ

全 active 銘柄の重要指標（市場全体データ + 個別銘柄データ）を取得し、
archive.important_indicators に書き込む。

ImportantIndicators/src/main.py は --ticker なしで全銘柄バッチモードに対応しており、
このファイルは venv の解決と subprocess 呼び出しを担う薄いラッパー。

Usage:
    python importantindicators_batch.py                  # 全銘柄
    python importantindicators_batch.py --ticker NVDA    # 特定銘柄のみ
"""
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
II_DIR = PROJECT_ROOT / "ImportantIndicators"


def _find_venv_python() -> str:
    for base in [II_DIR, II_DIR / "src"]:
        win = base / ".venv" / "Scripts" / "python.exe"
        unix = base / ".venv" / "bin" / "python"
        if win.exists():
            return str(win)
        if unix.exists():
            return str(unix)
    return "python"


def run(target_ticker: str | None = None) -> int:
    print(f"\n{'='*60}")
    print(f"=== ImportantIndicators Batch ===")
    print(f"{'='*60}")

    python = _find_venv_python()
    script = str(II_DIR / "src" / "main.py")

    cmd = [python, script]
    if target_ticker:
        cmd.extend(["--ticker", target_ticker])

    print(f"  起動: {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=str(II_DIR))

    if result.returncode != 0:
        print(f"  ImportantIndicators 失敗 (exit code: {result.returncode})")
    return result.returncode


if __name__ == "__main__":
    _ticker = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            _ticker = args[i + 1]
            i += 2
        else:
            i += 1

    sys.exit(run(_ticker))
