"""
portfolio_config YAML → DB 同期スクリプト

config/portfolio_config.yml を読み込み、portfolio_config テーブルに反映する。
システム管理カラム（id, updated_at, monitor_last_runs）は同期対象外。

使い方:
    python shared/sync_config.py              # DB に反映
    python shared/sync_config.py --dry-run    # 差分表示のみ（DB更新しない）
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from supabase_client import get_portfolio_config, update_portfolio_config

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "portfolio_config.yml"

SKIP_COLUMNS = {"id", "updated_at", "monitor_last_runs"}

JSONB_COLUMNS = {
    "stop_loss_pct", "price_tolerance_pct", "max_log_age_days",
    "max_allocation_pct", "monitor_schedules",
}


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_value(key: str, value):
    if key in JSONB_COLUMNS and isinstance(value, (dict, list)):
        return value
    return value


def compute_diff(yaml_data: dict, db_data: dict) -> dict:
    changes = {}
    for key, yaml_val in yaml_data.items():
        if key in SKIP_COLUMNS:
            continue
        yaml_val = normalize_value(key, yaml_val)
        db_val = db_data.get(key)

        if key in JSONB_COLUMNS:
            yaml_json = json.dumps(yaml_val, sort_keys=True, ensure_ascii=False)
            db_json = json.dumps(db_val, sort_keys=True, ensure_ascii=False) if db_val is not None else None
            if yaml_json != db_json:
                changes[key] = {"from": db_val, "to": yaml_val}
        else:
            if isinstance(db_val, str):
                try:
                    db_val_cmp = type(yaml_val)(db_val)
                except (ValueError, TypeError):
                    db_val_cmp = db_val
            else:
                db_val_cmp = db_val

            if yaml_val != db_val_cmp:
                changes[key] = {"from": db_val, "to": yaml_val}

    return changes


def print_diff(changes: dict):
    if not changes:
        print("差分なし。DB は YAML と同期済みです。")
        return

    print(f"{len(changes)} 件の差分を検出:\n")
    for key, diff in changes.items():
        from_val = diff["from"]
        to_val = diff["to"]
        if isinstance(to_val, (dict, list)):
            print(f"  {key}:")
            print(f"    - {json.dumps(from_val, ensure_ascii=False)}")
            print(f"    + {json.dumps(to_val, ensure_ascii=False)}")
        else:
            print(f"  {key}: {from_val} → {to_val}")


def sync(dry_run: bool = False):
    if not CONFIG_PATH.exists():
        print(f"[ERROR] {CONFIG_PATH} が見つかりません")
        sys.exit(1)

    yaml_data = load_yaml(CONFIG_PATH)
    db_data = get_portfolio_config()

    if not db_data:
        print("[ERROR] portfolio_config テーブルにデータがありません")
        sys.exit(1)

    changes = compute_diff(yaml_data, db_data)
    print_diff(changes)

    if not changes:
        return

    if dry_run:
        print("\n--dry-run: DB 更新をスキップしました。")
        return

    update_fields = {key: diff["to"] for key, diff in changes.items()}
    update_portfolio_config(**update_fields)
    print("\nDB を更新しました。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="portfolio_config YAML → DB 同期")
    parser.add_argument("--dry-run", action="store_true", help="差分表示のみ（DB更新しない）")
    args = parser.parse_args()
    sync(dry_run=args.dry_run)
