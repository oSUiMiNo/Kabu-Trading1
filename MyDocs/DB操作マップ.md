# DB操作マップ

各ブロック・小ブロック（ファイル）がDBのどのテーブル・カラムを読み書きしているか、**実行順**に記載した一覧。

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
| event_master | 経済・決算イベントのマスター（親） |
| event_date_time | イベントの日時スケジュール（子。event_master に紐づく） |
| monitor_schedule | 監視チェックポイントのスケジュール |
| event_scheduler_log | EventScheduler 実行の監査ログ |
| archive_reviews | archive の品質レビュー（NightWorker 出力） |
| words | 用語集（Discord Bot 用） |

---

## パイプライン順の操作マップ

### 1. Technical

#### technical_batch.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | watchlist | ticker, active, market | バッチ対象銘柄の取得 |

#### Technical/src/main.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | id, prev_plan_ids | `create_archivelog` 内部で前回プランIDを取得し `prev_plan_ids` チェーンを構築（※プラン中身は不使用） |
| 2 | 読取 | holdings | shares | mode 判定（shares > 0 → "review", それ以外 → "buy"） |
| 3 | 書込 | archive | id, ticker, mode, span, status, prev_plan_ids | `create_archivelog` でレコード新規作成（status="running"） |
| 4 | 書込 | archive | technical | テクニカル指標（latest_price, timeframes, usd_jpy_rate, holdings_snapshot 等） |
| 5 | 書込 | archive | status | "completed" or "error" |

※ `create_archive=False` の場合はステップ1-3 の代わりに `technical IS NULL` の既存 archive を検索

---

### 2. ImportantIndicators

#### ImportantIndicators/src/main.py（subprocess で呼ばれる）

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | important_indicators | 前回値（連続失敗検知） |
| 2 | 読取 | event_master | * | イベントリスク算出用 |
| 3 | 読取 | event_date_time | * | 直近イベント日時 |
| 4 | 書込 | archive | important_indicators | 市場指標（VIX, 金利, EPS, 出来高比, 相対強度等） |

---

### 3. Monitor

#### monitor_batch.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | watchlist | ticker, active, market | バッチ対象銘柄 |
| 2 | 読取 | archive | span | span フィルタリング |

#### Monitor/src/main.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | newplan_full | `get_latest_archivelog_with_newplan` で前回プラン取得 |
| 2 | 読取 | archive | technical, important_indicators | 株価・テクニカル指標・市場指標 |
| 3 | 読取 | archive | technical.holdings_snapshot | 保有状況（プロンプトに注入。スタンドアロン時は holdings を直接参照） |
| 4 | ── | （エージェント呼び出し：WebSearch でチェック実行） | | |
| 5 | 読取 | archive | technical.latest_price | current_price のソース（tech_price 優先） |
| 6 | 書込 | archive | monitor | {result, current_price, plan_price, price_change_pct, summary, risk_flags, ng_reason, cost_usd} |
| 7 | 書込 | archive | MotivationID | 0（OK） or 1（NG） |
| 8 | 書込 | archive | motivation_full | NG理由テキスト |
| 9 | 書込 | archive | active | OK→false, NG→true（後続ブロック起動制御） |
| 10 | 書込 | archive | status | "completed" or "error" |

#### Monitor/src/event_watch_check.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | monitor_schedule | watch_kind, market, consumed, watch_id | 未消化スケジュール検出 |
| 2 | 書込 | monitor_schedule | consumed, watched_at_utc | チェック済みマーク |
| 3 | 読取 | portfolio_config | monitor_schedules, monitor_last_runs | 定期スケジュール設定 |
| 4 | 書込 | portfolio_config | monitor_last_runs | 実行タイムスタンプ記録 |

---

### 4. Analyzer

#### analyzer_batch.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | id, ticker, active | `fetch_active_for_analyzer`（NG かつ active な銘柄） |
| 2 | 読取 | archive | mode | mode 参照（Technical が設定済み） |

#### Analyzer/src/main.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | technical, monitor, important_indicators | マーケットコンテキスト構築 |
| 2 | 読取 | portfolio_config | analyzer_num_lanes, analyzer_max_rounds, analyzer_opinions_per_lane | Analyzer 設定 |
| 3 | 読取 | archive | technical.holdings_snapshot | 保有状況をコンテキストに含める |
| 4 | ── | （複数レーン並行：Analyst ↔ DA 議論 → Opinion → Judge） | | |
| 5 | 書込 | archive | lanes | 各レーンの議論ログ（opinion, judge, discussion_md, agreement） |
| 6 | ── | （Final Judge 実行） | | |
| 7 | 書込 | archive | final_judge | 最終判定（decision, vote_for, vote_against, decision_basis） |
| 8 | 書込 | archive | verdict | 判定文字列（BUY, SELL, HOLD, ADD, REDUCE 等） |
| 9 | 書込 | archive | status | "completed" or "error" |

---

### 5. Planning

#### planning_batch.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | id, ticker, active, span | `fetch_active_for_planning`（Analyzer 完了かつ active な銘柄） |
| 2 | 読取 | portfolio_config | total_budget_jpy | 予算 |

#### Planning/src/main.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | lanes, final_judge | 議論結果 |
| 2 | 読取 | archive | technical | 株価・テクニカル指標 |
| 3 | 読取 | archive | monitor | 監視結果（価格変動） |
| 4 | 読取 | archive | important_indicators | 市場指標 |
| 5 | 読取 | archive | technical.holdings_snapshot | 保有状況（ポジションサイジング） |
| 6 | 読取 | portfolio_config | total_budget_jpy, risk_limit_pct, stop_loss_pct, default_take_profit_pct, max_allocation_pct, price_block_pct, max_log_age_days, min_rr_ratio | 予算・リスク設定 |
| 7 | 読取 | event_master | * | リスクオーバーレイ |
| 8 | ── | （鮮度チェック → 価格ズレ判定 → confidence 算出 → 配分計算 → YAML 生成） | | |
| 9 | 書込 | archive | newplan_full | プランYAML全文 |
| 10 | 書込 | archive | verdict | 判定文字列（Analyzer の verdict を Planning の計算結果で上書き） |
| 11 | 書込 | archive | status | "completed" or "error" |

---

### 6. Watch

#### watch_batch.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | id, ticker, active | `fetch_active_for_watch`（Planning 完了かつ active な銘柄） |

#### Watch/src/main.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | newplan_full, verdict, monitor, final_judge, technical, created_at, mode | アーカイブログ取得 |
| 2 | 読取 | archive | （前回分） | `get_previous_archivelog_with_newplan` で旧プラン比較用 |
| 3 | ── | （watch-summarizer エージェント呼び出し：サマリー・比較文生成） | | |
| 4 | 書込 | watchlist | MotivationID, motivation_summary, discussion_result, discussion_summary, new_plan_summary, risk_flags, plan_comparison, stock_price, latest_archive_id | watchlist 更新 |
| 5 | 読取 | watchlist | * | display_name 取得 |
| 6 | ── | （Discord 通知：mode に応じてラベル判定、保有中NG のみ送信） | | |
| 7 | 書込 | archive | active | false（処理完了マーク） |

---

### 7. main_pipeline.py（パイプラインオーケストレーター）

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | watchlist | ticker, active, market, display_name | 対象銘柄・表示名 |
| 2 | ── | （Phase 1: technical_batch.py 実行） | | |
| 3 | ── | （Phase 2: monitor_batch.py 実行） | | |
| 4 | 読取 | archive | id, ticker, monitor, mode, status | `fetch_monitor_results_since`（Monitor 結果取得） |
| 5 | ── | （ERROR/CHECK 銘柄に Discord 通知） | | |
| 6 | 読取 | archive | id, ticker, active, MotivationID | `fetch_active_for_analyzer`（NG 銘柄検出） |
| 7 | ── | （NG 銘柄なし → Phase 6 実行後 COMPLETE 通知で終了） | | |
| 8 | ── | （Phase 3: analyzer_batch.py 実行） | | |
| 9 | 読取 | archive | final_judge | `get_archivelog_by_id` で Analyzer 完了確認 |
| 10 | 書込 | archive | status, active | Analyzer 失敗時 → "failed" + active=false |
| 11 | ── | （Phase 4: planning_batch.py 実行） | | |
| 12 | 読取 | archive | id, ticker, active | `fetch_active_for_planning`（Planning 失敗検出） |
| 13 | 書込 | archive | status, active | Planning 失敗時 → "failed" + active=false |
| 14 | ── | （Phase 5: watch_batch.py 実行） | | |
| 15 | ── | （Phase 6: actionlog_batch.py 実行） | | |
| 16 | ── | （COMPLETE 通知） | | |

---

### 8. ActionLog（Phase 6 + Web UI + データサービス）

#### actionlog_batch.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | id, ticker, mode | `fetch_pending_for_actionlog`（action_log 未投入の archive） |
| 2 | 読取 | action_log | archive_id | 未投入チェック（fetch_pending_for_actionlog 内部） |

#### ActionLog/src/pipeline_main.py（Phase 6 — 1銘柄処理）

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | newplan_full, monitor, technical, mode, created_at | アーカイブログ取得 |
| 2 | 読取 | watchlist | market | 市場判定 |
| 3 | ── | （populate_action_log 呼び出し） | | |
| 4 | 読取 | action_log | * | 全件取得（holdings 再計算用） |
| 5 | 書込 | holdings | shares, avg_cost | `_sync_holdings_from_logs`（action_log から再計算） |

#### ActionLog/src/auto_populate.py — `populate_action_log()`

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | action_log | archive_id | 二重投入チェック |
| 2 | 読取 | holdings | shares, avg_cost | 保有状況参照 |
| 3 | 読取 | action_log | * | `list_all_action_logs`（total_shares 計算） |
| 4 | 読取 | action_log | * | `get_latest_action_log`（前回 cumulative_invested） |
| 5 | 書込 | action_log | ticker, archive_id, action_date, action_text, story, decision, quantity, price, ... | INSERT |

#### ActionLog/src/data_service.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | action_log | * | 月別・全件取得 |
| 2 | 読取 | holdings | shares, avg_cost | 現在状態参照 |
| 3 | 読取 | watchlist | * | 銘柄一覧 |
| 4 | 書込 | action_log | money_in, total_assets, pnl, story, action_text, user_overrides | UI 編集（カスケード再計算） |
| 5 | 書込 | holdings | shares, avg_cost | `_sync_holdings_from_logs`（全 action_log から再計算） |

#### ActionLog/src/handoff_service.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | action_log | * | 月別・全件取得 |
| 2 | 読取 | action_log_handoff | handoff_text | キャッシュ参照 |
| 3 | ── | （LLM で引き継ぎ文生成） | | |
| 4 | 書込 | action_log | story | LLM 生成のナラティブ |
| 5 | 書込 | action_log_handoff | ticker, year_month, handoff_text, generated_at | 引き継ぎ文 upsert |

---

### 9. NightWorker

#### NightWorker/src/main.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | archive | * | `fetch_unreviewed_archives`（6時間以上経過・未レビュー） |
| 2 | 読取 | archive_reviews | archive_id | 重複チェック |
| 3 | ── | （LLM で品質レビュー実行） | | |
| 4 | 書込 | archive_reviews | archive_id, overall_quality, issues_json, issue_url, review_cost_usd | レビュー結果 |

---

### 10. EventScheduler

#### EventScheduler/src/main.py

| # | 操作 | テーブル | カラム | 内容 |
|---|------|---------|--------|------|
| 1 | 読取 | event_master | * | 既存イベント確認 |
| 2 | 読取 | event_date_time | * | 既存スケジュール確認 |
| 3 | ── | （外部API / LLM でイベント情報収集） | | |
| 4 | 書込 | event_master | event_id, name_ja, region, ... | イベントマスター upsert |
| 5 | 書込 | event_date_time | event_id, occurrence_id, scheduled_date_local, ... | 日時スケジュール upsert |
| 6 | 書込 | monitor_schedule | occurrence_id, watch_kind, market, watch_id | 監視スケジュール upsert |
| 7 | 書込 | event_scheduler_log | run_type, status, ... | 実行ログ |

---

## テーブル別 書き込み元まとめ

| テーブル | 書き込むブロック（小ブロック） |
|---------|---------------------------|
| **archive** | Technical（main.py）、Monitor（main.py）、Analyzer（main.py）、Planning（main.py）、Watch（main.py）、main_pipeline.py |
| **watchlist** | Watch（main.py）のみ |
| **holdings** | ActionLog（data_service.py, auto_populate.py, pipeline_main.py） |
| **portfolio_config** | Monitor（event_watch_check.py）のみ（monitor_last_runs） |
| **action_log** | ActionLog（pipeline_main.py → auto_populate.py, data_service.py, handoff_service.py） |
| **action_log_handoff** | ActionLog（handoff_service.py）のみ |
| **event_master** | EventScheduler のみ |
| **event_date_time** | EventScheduler のみ |
| **monitor_schedule** | EventScheduler、Monitor（event_watch_check.py） |
| **event_scheduler_log** | EventScheduler のみ |
| **archive_reviews** | NightWorker のみ |
| **words** | Discord Bot（Edge Function）のみ |

## テーブル別 参照元まとめ

| テーブル | 参照するブロック（小ブロック） |
|---------|---------------------------|
| **archive** | Technical（main.py）、Monitor（main.py）、Analyzer（main.py）、Planning（main.py）、Watch（main.py）、main_pipeline.py、NightWorker（main.py）、各 batch ファイル |
| **watchlist** | 全 batch ファイル、main_pipeline.py、Watch（main.py）、ActionLog（auto_populate.py, data_service.py） |
| **holdings** | Technical（main.py）、Monitor（main.py：スタンドアロン実行時のみ）、ActionLog（auto_populate.py, data_service.py, pipeline_main.py） |
| **portfolio_config** | Monitor（main.py, event_watch_check.py）、Analyzer（main.py）、Planning（main.py）、planning_batch.py |
| **action_log** | ActionLog（pipeline_main.py, auto_populate.py, data_service.py, handoff_service.py） |
| **action_log_handoff** | ActionLog（handoff_service.py）のみ |
| **event_master** | ImportantIndicators（event_risk.py）、Planning（risk_policy.py）、EventScheduler |
| **event_date_time** | ImportantIndicators（event_risk.py）、EventScheduler |
| **monitor_schedule** | Monitor（event_watch_check.py）のみ |
| **event_scheduler_log** | EventScheduler のみ |
| **archive_reviews** | NightWorker のみ |
| **words** | Discord Bot（Edge Function）のみ |
