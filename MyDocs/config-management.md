# 設定管理（portfolio_config）

## 概要

投資パラメータ、Discussion の議論設定、Monitor の実行スケジュールなど、
ユーザーが調整したい値を Supabase の `portfolio_config` テーブル1行に集約している。

値の変更方法は2つ:
- **YAML 編集 → push**（推奨）: `config/portfolio_config.yml` を編集して push すると自動で DB に反映
- **Supabase ダッシュボード**: テーブルを直接編集（即時反映だが変更履歴が残らない）


## YAML 管理の仕組み

```
config/portfolio_config.yml   ──push──→   sync-config.yml（GitHub Actions）
                                                │
                                          sync_config.py
                                                │
                                                ▼
                                        portfolio_config テーブル
```

### ファイル構成

| ファイル | 役割 |
|----------|------|
| `config/portfolio_config.yml` | 設定の定義ファイル（人が編集する） |
| `shared/sync_config.py` | YAML → DB 同期スクリプト |
| `.github/workflows/sync-config.yml` | push 時の自動同期ワークフロー |

### ローカルからの操作

```bash
python shared/sync_config.py --dry-run   # 差分確認（DB更新しない）
python shared/sync_config.py             # DB に反映
```


## 設定項目一覧

### 投資パラメータ

| YAML キー | 型 | 説明 |
|-----------|-----|------|
| `total_budget_jpy` | 数値 | 総投資予算（円） |
| `risk_limit_pct` | 数値 | 1銘柄あたりの最大損失許容（%） |
| `default_take_profit_pct` | 数値 | 利確ライン（%） |
| `stop_loss_pct` | {short, mid, long} | 損切り幅（投資期間別 %） |
| `price_tolerance_pct` | {short, mid, long} | 価格ズレ許容幅（投資期間別 %） |
| `price_block_pct` | 数値 | 価格ズレ停止閾値（%）。この%以上乖離でプラン停止 |
| `max_log_age_days` | {short, mid, long} | ログ鮮度上限（投資期間別 日数） |
| `max_allocation_pct` | {low, med, high} | 1銘柄への配分上限（confidence別 %） |
| `notes` | 文字列 | メモ欄 |

### Discussion パラメータ

| YAML キー | 型 | 説明 |
|-----------|-----|------|
| `discussion_num_lanes` | 整数 | 並行レーン数（デフォルト: 2） |
| `discussion_max_rounds` | 整数 | 1レーンあたりの最大議論ラウンド数（デフォルト: 4） |
| `discussion_opinions_per_lane` | 整数 | 1レーンあたりの意見体数（デフォルト: 2） |

CLI 引数で明示指定した場合はそちらが優先される（DB 値はフォールバック）。

### Monitor 定期スケジュール

| YAML キー | 型 | 説明 |
|-----------|-----|------|
| `monitor_schedule_enabled` | 真偽値 | 定期スケジュール全体の有効/無効（緊急停止スイッチ） |
| `monitor_schedules` | 配列 | 定期スケジュール定義（下記参照） |

`monitor_schedules` の各要素:

```yaml
- label: JP_AM                    # 識別名（重複防止のキーにもなる）
  description: "説明文"            # 人間向けの説明
  market: JP                      # "JP" / "US" / null（null=全銘柄）
  hour_utc: 1                     # 実行時刻（UTC の時）
  minute_utc: 10                  # 実行時刻（UTC の分）
  days_of_week: [0, 1, 2, 3, 4]  # 曜日（0=月 ... 6=日）
  skip_spans: []                  # スキップする投資期間（["long"] で長期省略）
```

### YAML 管理対象外のカラム

| カラム | 理由 |
|--------|------|
| `id` | システム PK |
| `updated_at` | 自動更新タイムスタンプ |
| `monitor_last_runs` | event_watch_check.py が自動管理（重複実行防止用） |


## Monitor の自動実行の仕組み

定期 Monitor は `event-monitor.yml`（5分ポーリング）に統合されている。

```
event-monitor.yml（5分ごと cron）
    │
    ▼
event_watch_check.py
    ├── 1. monitor_schedule テーブルからイベント watch を検索
    ├── 2. portfolio_config.monitor_schedules から定期スケジュールを評価
    │      （下記マッチ判定パラメータ参照）
    │      （monitor_last_runs で重複を防止）
    └── 3. どちらかマッチしたら → ng_dispatch.py を起動
                                    │
                                    ▼
                              Monitor → Discussion → Planning パイプライン
```

### マッチ判定パラメータ

GitHub Actions の cron は5分間隔で設定しているが、実際は数分〜最大78分程度の遅延が発生する。
そのため予定時刻に対して広い許容窓を設けている。

| 設定 | 値 | 意味 |
|------|-----|------|
| -方向（前） | 40分 | 予定の40分前のcronでも拾う |
| +方向（後） | 90分 | 最大90分の遅延をカバー |
| 重複防止 | 150分 | 窓の合計130分を超える値で二重発火防止 |

`monitor.yml` は手動実行（`workflow_dispatch`）専用。テストや特定銘柄チェック用。


## 認証

GitHub Actions では `CLAUDE_CODE_OAUTH_TOKEN`（Max プランの OAuth トークン）を使用。
`claude setup-token` で取得し、GitHub Secrets に登録する。
