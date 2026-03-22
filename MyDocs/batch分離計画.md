# Dispatcher 分離計画：各ブロックの batch 切り出し

## Context

各ブロックの `main.py` が「1銘柄の処理」と「複数銘柄の検出・並列実行」を兼務している。
これを分離し、複数銘柄ループを `<block>_batch.py`（PJTルート）に切り出す。
併せて Monitor だけ直接 import だった不統一を解消し、全ブロック subprocess + DB 参照に統一する。

---

## 新規作成ファイル

| ファイル | 役割 |
|---------|------|
| `technical_batch.py` | Technical の複数銘柄バッチ |
| `monitor_batch.py` | Monitor の複数銘柄バッチ |
| `analyzer_batch.py` | Analyzer の複数銘柄バッチ |
| `planning_batch.py` | Planning の複数銘柄バッチ |
| `watch_batch.py` | Watch の複数銘柄バッチ |

---

## 変更ファイル

| ファイル | 変更内容 |
|---------|---------|
| `main_pipeline.py` | Monitor 直接 import 廃止、全ブロック subprocess で batch を呼ぶ形に統一 |
| `Technical/src/main.py` | バッチ部分（`run_technical` 内の watchlist 取得→gather）を削除、`--ticker` 必須化 |
| `Monitor/src/main.py` | バッチ部分（`run_monitor` 内の watchlist 取得→gather）と `MonitorSummary` を削除、`--ticker` 必須化 |
| `Analyzer/src/main.py` | `run_batch()` 削除、`--archive-id` 引数追加 |
| `Planning/src/main.py` | `run_batch()` 削除 |
| `Watch/src/main.py` | バッチ部分（`run_watch` 内の fetch→task_group）を削除、`--ticker` 必須化 |
| `shared/supabase_client.py` | `fetch_today_monitor_results()` 追加 |

---

## Step 1：technical_batch.py

> batch_base.py は廃止。venv 検出・並列実行ロジックは各 batch ファイルにインラインで記述する。

```
1. list_watchlist(active_only=True) でティッカー取得（--ticker 指定時はその1銘柄のみ）
2. Technical/.venv の Python で Technical/src/main.py --ticker <T> を並列実行
3. 成功/失敗カウント出力、exit code 返却
```

**Technical/src/main.py の変更：**
- `run_technical()` のバッチ分岐（watchlist 取得 → asyncio.gather）を削除
- `process_one_ticker()` はそのまま
- `__main__` は `--ticker` 必須に変更

---

## Step 2：monitor_batch.py

```
1. list_watchlist(active_only=True, market=market) でティッカー取得
2. skip_spans フィルタリング（archivelog から span を読んでスキップ判定）
3. Monitor/src/.venv の Python で Monitor/src/main.py --ticker <T> を並列実行
4. 成功/失敗カウント出力、exit code 返却
```

**Monitor/src/main.py の変更：**
- `run_monitor()` のバッチ部分を削除
- 単一銘柄用の `run_single(ticker, market)` を残す
  - 既存の前処理（archivelog 取得、technical_map 検出）を1銘柄分だけ実行 → `check_one_ticker()` 呼び出し
- `MonitorSummary` クラスは削除（main_pipeline が DB から取得するため不要）
- `__main__` は `--ticker` 必須

---

## Step 3：shared/supabase_client.py に新関数追加

**`fetch_today_monitor_results() -> list[dict]`**

main_pipeline が Monitor 後に DB から結果を取得するための関数。

```python
def fetch_today_monitor_results() -> list[dict]:
    """今日の Monitor 結果（archive.monitor IS NOT NULL）を返す"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    resp = (
        get_client()
        .from_("archive")
        .select("ticker, monitor, status")
        .gte("created_at", f"{today}T00:00:00+09:00")
        .not_.is_("monitor", "null")
        .execute()
    )
    return resp.data or []
```

用途：
- `monitor.retries_exhausted == True` → ERROR 通知
- `monitor.result == "OK"` かつ `monitor.risk_flags` あり → CHECK 通知
- `display_names` は `list_watchlist()` から別途取得

---

## Step 4：analyzer_batch.py

```
1. fetch_active_for_analyzer() で対象銘柄を取得
2. list_watchlist() で display_name を取得
3. Analyzer/src/.venv の Python で Analyzer/src/main.py <ticker> <horizon> <mode> --archive-id <id> を並列実行
```

**Analyzer/src/main.py の変更：**
- `run_batch()` を削除
- `__main__` に `--archive-id` 引数を追加（既存 archive に書き込むため）
- 引数なし呼び出しはエラーメッセージ

---

## Step 5：planning_batch.py

```
1. fetch_active_for_planning() で対象銘柄を取得
2. get_portfolio_config() で budget/risk_limit を取得
3. Planning/src/.venv の Python で Planning/src/main.py <ticker> <horizon> [budget] [risk_limit] を並列実行
```

**Planning/src/main.py の変更：**
- `run_batch()` を削除
- 手動指定モード（引数あり）はそのまま維持

---

## Step 6：watch_batch.py

```
1. fetch_active_for_watch() で対象銘柄を取得
2. Watch/src/.venv の Python で Watch/src/main.py --ticker <T> を並列実行
```

**Watch/src/main.py の変更：**
- `run_watch()` のバッチ分岐を削除
- `process_one_ticker()` はそのまま
- `__main__` は `--ticker` 必須

---

## Step 7：main_pipeline.py の書き換え

**削除するもの：**
- `from main import run_monitor`
- `sys.path.insert(0, str(PROJECT_ROOT / "Monitor" / "src"))`
- `_find_venv_python()`（batch_base.py に移動）
- 各ブロックの `run_*()` 関数（subprocess 直接呼び出しに置換）

**新しいフロー：**

```python
# Phase 1: Technical
subprocess.run(["python", "technical_batch.py"])

# Phase 2: Monitor
dn_map = {w["ticker"]: w.get("display_name") or w["ticker"] for w in safe_db(list_watchlist)}
subprocess.run(["python", "monitor_batch.py", "--market", market, ...])
# DB から結果取得
monitor_results = safe_db(fetch_today_monitor_results)
for rec in monitor_results:
    monitor_data = rec["monitor"]
    if monitor_data.get("retries_exhausted"):
        # ERROR 通知
    elif classify_label(monitor_data) == NotifyLabel.CHECK:
        # CHECK 通知
ng_tickers = safe_db(fetch_active_for_analyzer)
# NG なければ COMPLETE 通知して終了

# Phase 3: Analyzer
subprocess.run(["python", "analyzer_batch.py"])
# DB で final_judge 確認（既存ロジックそのまま）

# Phase 4: Planning
subprocess.run(["python", "planning_batch.py"])
# DB で newplan_full 確認（既存ロジックそのまま）

# Phase 5: Watch
subprocess.run(["python", "watch_batch.py"])
# COMPLETE 通知
```

---

## Step 8：ドキュメント・ワークフロー・メモリ更新

- `.github/workflows/` — batch ファイル名に更新（monitor.yml, event-monitor.yml）
- `README.md` — モジュール構成表に batch ファイル追加
- `Monitor/.claude/CLAUDE.md`, `Planning/.claude/CLAUDE.md` 等 — 実行方法更新
- `MEMORY.md` — 命名規則に batch の説明を追加

---

## Step 9：検証

1. 各 batch の単体テスト（`--ticker` 指定で1銘柄実行）
2. 各ブロック main.py が `--ticker` なしでエラーになることを確認
3. main_pipeline.py の構文チェック
4. Monitor → DB → main_pipeline の通知フロー確認（`retries_exhausted`, CHECK の検出）
5. 全旧関数名（`run_batch`, `run_monitor`, `MonitorSummary`）の grep で残存確認

---

## CLI 引数フロー

```
main_pipeline.py --market US --skip-span long
  └─ monitor_batch.py --market US --skip-span long
       └─ Monitor/src/main.py --ticker NVDA --market US
       └─ Monitor/src/main.py --ticker AAPL --market US
```

- main_pipeline → batch：--market, --skip-span, --ticker をそのまま渡す
- batch → block main.py：--ticker は必須、他はブロックが必要な引数のみ

---

## 編集不可ブロックの承認

CLAUDE.md で編集不可に指定されている以下のブロックは、ユーザー承認の上で変更する：
- `Analyzer/src/main.py` — run_batch() 削除、--archive-id 追加
- `Monitor/src/main.py` — バッチ部分削除、MonitorSummary 削除
- `Planning/src/main.py` — run_batch() 削除
- `EventScheduler` — 変更なし（対象外）
