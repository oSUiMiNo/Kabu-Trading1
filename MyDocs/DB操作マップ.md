# DB操作マップ

各ブロック・小ブロック（ファイル）がDBのどのテーブル・カラムに書き込み／読み取りしているかの一覧。

---

## テーブル一覧

| テーブル | 役割 |
|---------|------|
| archive | パイプライン1実行=1行。各ブロックの出力をJSONBで格納する中央ステージング |
| watchlist | 監視対象銘柄のマスター + 最新状況サマリー |
| holdings | 銘柄別の保有状況（株数・平均単価・現在価格） |
| portfolio_config | 投資設定（シングルトン。予算・リスク・スケジュール等） |
| action_log | 月次アクション履歴（売買記録・損益追跡） |
| action_log_handoff | 月次引き継ぎサマリー（キャッシュ） |
| event_master | 経済・決算イベントのマスター |
| event_date_time | イベントの日時スケジュール |
| monitor_schedule | 監視チェックポイントのスケジュール |
| event_scheduler_log | EventScheduler 実行の監査ログ |
| archive_reviews | archive の品質レビュー（NightWorker 出力） |
| words | 用語集（Discord Bot 用） |

---

## パイプライン順の操作マップ

### 1. Technical

#### Technical/src/main.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | archive | id, ticker, mode, span, status, prev_plan_ids | `create_archivelog` でレコード新規作成 |
| 書込 | archive | technical | テクニカル指標（latest_price, timeframes, usd_jpy_rate 等） |
| 書込 | archive | status | "running" → "completed" or "error" |
| 読取 | archive | id, prev_plan_ids | `create_archivelog` 内部で前回プランIDを取得し `prev_plan_ids` チェーンを構築（プラン中身は不使用） |
| 読取 | holdings | shares | mode 判定（shares > 0 → "review", それ以外 → "buy"） |
| 読取 | watchlist | * | 対象銘柄一覧 |

#### technical_batch.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 読取 | watchlist | ticker, active, market | バッチ対象銘柄の取得 |

---

### 2. ImportantIndicators

#### ImportantIndicators/src/main.py（subprocess で呼ばれる）

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | archive | important_indicators | 市場指標（VIX, 金利, EPS, 出来高比, 相対強度等） |
| 読取 | archive | important_indicators | 前回値（連続失敗検知） |
| 読取 | event_master | * | イベントリスク算出用 |
| 読取 | event_date_time | * | 直近イベント日時 |

---

### 3. Monitor

#### Monitor/src/main.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | archive | monitor | {result, current_price, plan_price, price_change_pct, summary, risk_flags, ng_reason, cost_usd} |
| 書込 | archive | MotivationID | 0（OK） or 1（NG） |
| 書込 | archive | motivation_full | NG理由テキスト |
| 書込 | archive | active | OK→false, NG→true（後続ブロック起動制御） |
| 書込 | archive | status | "completed" or "error" |
| 読取 | archive | newplan_full | `get_latest_archivelog_with_newplan` で前回プラン |
| 読取 | archive | technical | 株価・テクニカル指標 |
| 読取 | archive | important_indicators | 市場指標 |
| 読取 | holdings | shares, avg_cost | 保有状況（プロンプトに注入） |
| 読取 | portfolio_config | display_names | 銘柄表示名 |

#### monitor_batch.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 読取 | watchlist | ticker, active, market | バッチ対象銘柄 |
| 読取 | archive | span | span フィルタリング |

#### Monitor/src/event_watch_check.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 読取 | monitor_schedule | watch_kind, market, consumed, watch_id | 未消化スケジュール検出 |
| 書込 | monitor_schedule | consumed, watched_at_utc | チェック済みマーク |
| 読取 | portfolio_config | monitor_schedules, monitor_last_runs | 定期スケジュール設定 |
| 書込 | portfolio_config | monitor_last_runs | 実行タイムスタンプ記録 |

---

### 4. Analyzer

#### Analyzer/src/main.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | archive | lanes | 各レーンの議論ログ（opinion, judge, discussion_md, agreement） |
| 書込 | archive | final_judge | 最終判定（decision, vote_for, vote_against, decision_basis） |
| 書込 | archive | verdict | 判定文字列（BUY, SELL, HOLD, ADD, REDUCE 等） |
| 書込 | archive | status | "completed" or "error" |
| 読取 | archive | technical | 株価・テクニカル指標 |
| 読取 | archive | monitor | 監視結果 |
| 読取 | archive | important_indicators | 市場指標 |
| 読取 | portfolio_config | analyzer_num_lanes, analyzer_max_rounds, analyzer_opinions_per_lane | Analyzer 設定 |
| 読取 | holdings | shares | mode 判定 |

#### analyzer_batch.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 読取 | archive | id, ticker, active | `fetch_active_for_analyzer`（NG かつ active な銘柄） |
| 読取 | holdings | shares | mode 判定（"review" / "buy"） |

---

### 5. Planning

#### Planning/src/main.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | archive | newplan_full | プランYAML全文（decision, freshness, data_checks, risk_management, portfolio_plan, execution_plan） |
| 書込 | archive | verdict | 判定文字列 |
| 書込 | archive | status | "completed" or "error" |
| 読取 | archive | lanes, final_judge | 議論結果 |
| 読取 | archive | technical | 株価・テクニカル指標 |
| 読取 | archive | monitor | 監視結果（価格変動） |
| 読取 | archive | important_indicators | 市場指標 |
| 読取 | portfolio_config | total_budget_jpy, risk_limit_pct, stop_loss_pct, default_take_profit_pct, max_allocation_pct, price_block_pct, max_log_age_days, min_rr_ratio | 予算・リスク設定 |
| 読取 | holdings | shares, avg_cost | 保有状況（ポジションサイジング） |
| 読取 | event_master | * | リスクオーバーレイ |

#### planning_batch.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 読取 | archive | id, ticker, active, span | `fetch_active_for_planning`（Analyzer 完了かつ active な銘柄） |
| 読取 | portfolio_config | total_budget_jpy | 予算 |

---

### 6. Watch

#### Watch/src/main.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | watchlist | MotivationID, motivation_summary, discussion_result, discussion_summary, new_plan_summary, risk_flags, plan_comparison, stock_price, latest_archive_id | watchlist 更新 |
| 書込 | archive | active | false（処理完了マーク） |
| 読取 | archive | newplan_full, verdict, monitor, final_judge, technical, created_at, mode | アーカイブログ参照 |
| 読取 | watchlist | * | display_name 取得、market 取得 |
| 読取 | holdings | shares, avg_cost | holdings 同期用 |
| 読取 | action_log | * | `list_all_action_logs`（holdings 同期用） |

**Watch 経由で呼ばれる ActionLog 関数：**

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | action_log | ticker, archive_id, action_date, action_text, story, decision, quantity, price, money_in, cumulative_invested, total_assets, pnl, is_auto | `populate_from_archive`（自動投入） |
| 書込 | holdings | shares, avg_cost | `_sync_holdings_from_logs`（action_log から再計算） |

#### watch_batch.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 読取 | archive | id, ticker, active | `fetch_active_for_watch`（Planning 完了かつ active な銘柄） |

---

### 7. main_pipeline.py（パイプラインオーケストレーター）

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 読取 | watchlist | ticker, active, market, display_name | 対象銘柄・表示名 |
| 読取 | archive | id, ticker, monitor, mode, status | `fetch_monitor_results_since`（Monitor 結果取得） |
| 読取 | archive | id, ticker, active, final_judge | `fetch_active_for_analyzer`（NG 銘柄検出） |
| 読取 | archive | id, ticker, active | `fetch_active_for_planning`（Planning 失敗検出） |
| 書込 | archive | status, active | 失敗レコードを "failed" + active=false に更新 |
| 書込 | action_log | ticker, archive_id, action_date, action_text, story, decision, ... | 保有中+OK → `populate_from_monitor`（プラン継続の記録） |

---

### 8. ActionLog（Web UI + データサービス）

#### ActionLog/src/auto_populate.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | action_log | ticker, archive_id, action_date, action_text, story, decision, quantity, price, money_in, cumulative_invested, total_assets, pnl, is_auto, user_overrides | archive → action_log 変換 |
| 読取 | action_log | archive_id | 二重投入チェック |
| 読取 | action_log | * | 最新レコード（cumulative_invested 算出）、全レコード（株数計算） |
| 読取 | holdings | shares, avg_cost | 保有状況参照 |
| 読取 | watchlist | market | 市場判定 |

#### ActionLog/src/data_service.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | action_log | money_in, total_assets, pnl, story, action_text, user_overrides | UI からの編集（カスケード再計算） |
| 書込 | holdings | shares, avg_cost | `_sync_holdings_from_logs`（全 action_log から再計算） |
| 読取 | action_log | * | 月別取得、全件取得 |
| 読取 | holdings | shares, avg_cost | 現在状態参照 |
| 読取 | watchlist | * | 銘柄一覧 |

#### ActionLog/src/handoff_service.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | action_log_handoff | ticker, year_month, handoff_text, generated_at | 引き継ぎ文 upsert |
| 書込 | action_log | story | LLM 生成のナラティブ |
| 読取 | action_log | * | 月別・全件取得 |
| 読取 | action_log_handoff | handoff_text | キャッシュ参照 |

---

### 9. NightWorker

#### NightWorker/src/main.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | archive_reviews | archive_id, overall_quality, issues_json, issue_url, review_cost_usd | 品質レビュー結果 |
| 読取 | archive | * | `fetch_unreviewed_archives`（6時間以上経過・未レビュー） |
| 読取 | archive_reviews | archive_id | 重複チェック |

---

### 10. EventScheduler

#### EventScheduler/src/main.py

| 操作 | テーブル | カラム | 内容 |
|------|---------|--------|------|
| 書込 | event_master | event_id, name_ja, region, ... | イベントマスター upsert |
| 書込 | event_date_time | event_id, occurrence_id, scheduled_date_local, ... | 日時スケジュール upsert |
| 書込 | monitor_schedule | occurrence_id, watch_kind, market, watch_id | 監視スケジュール upsert |
| 書込 | event_scheduler_log | run_type, status, ... | 実行ログ |
| 読取 | event_master | * | 既存イベント確認 |
| 読取 | event_date_time | * | 既存スケジュール確認 |

---

## テーブル別 書き込み元まとめ

| テーブル | 書き込むブロック（小ブロック） |
|---------|---------------------------|
| **archive** | Technical（main.py）、Monitor（main.py）、Analyzer（main.py）、Planning（main.py）、Watch（main.py）、main_pipeline.py |
| **watchlist** | Watch（main.py）のみ |
| **holdings** | ActionLog（data_service.py, auto_populate.py）、Watch（main.py 経由） |
| **portfolio_config** | Monitor（event_watch_check.py）のみ（monitor_last_runs） |
| **action_log** | Watch（main.py → auto_populate.py）、main_pipeline.py（→ populate_from_monitor）、ActionLog（data_service.py, handoff_service.py） |
| **action_log_handoff** | ActionLog（handoff_service.py）のみ |
| **event_master** | EventScheduler のみ |
| **event_date_time** | EventScheduler のみ |
| **monitor_schedule** | EventScheduler、Monitor（event_watch_check.py） |
| **event_scheduler_log** | EventScheduler のみ |
| **archive_reviews** | NightWorker のみ |
| **words** | Discord Bot（Edge Function）のみ |
