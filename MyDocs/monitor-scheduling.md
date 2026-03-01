# 定期 Monitor のスケジューリング

## 全体像

定期 Monitor は GitHub Actions の固定 cron（5分間隔）と、
DB に定義されたスケジュールの「ポーリング照合」で動作する。

```
GitHub Actions cron（5分間隔）
    │
    │  毎回必ず起動（時刻の判断はしない）
    ▼
event_watch_check.py
    │
    │  DB の monitor_schedules を読み、
    │  「今の時刻にマッチするか？」を判定
    │
    ├── マッチなし → 即終了（数秒で完了）
    └── マッチあり → ng_dispatch.py で Monitor パイプライン起動
```

GitHub Actions は「5分ごとに起動する」だけで、スケジュール時刻を知らない。
`event_watch_check.py` が毎回 DB を確認しに行き、時刻が合えば実行するポーリング方式。


## スケジュール定義

`portfolio_config.monitor_schedules`（JSONB 配列）に定義する。
YAML 管理の場合は `config/portfolio_config.yml` を編集して push。

```yaml
- label: US_AM                           # 識別名（重複防止のキーにもなる）
  description: "平日 US 1回目"            # 人間向けの説明
  market: US                             # 対象市場（JP / US / null=全銘柄）
  hour_utc: 15                           # 実行時刻（UTC の時）
  minute_utc: 0                          # 実行時刻（UTC の分）
  days_of_week: [6, 0, 1, 2, 3]         # 曜日（0=月 ... 6=日）
  skip_spans: []                         # スキップする投資期間
```


## 時刻マッチング

### なぜ「ぴったり一致」ではないか

GitHub Actions の cron は共有ランナーで実行されるため、
設定した間隔通りには動かない。5分間隔を指定しても実際は 5〜22分程度のばらつきがある。

そのため、予定時刻との差に許容幅を設けている。

### 許容幅（非対称）

```
        予定の7分前              予定時刻              予定の25分後
            │                      │                      │
  ──────────[==============================================]──────────
            ↑                                             ↑
         早い方の限度                              遅い方の限度
         （cron が早く来ることは稀）        （cron の遅延は日常的）
```

- **前方（早い方）: 7分** — cron が予定より早く発火することは稀なので狭くてよい
- **後方（遅い方）: 25分** — GitHub Actions の遅延は20分超もあるため広くとる

判定コード（`shared/supabase_client.py` の `get_due_regular_schedules()`）:

```python
diff = current_minute - target_minute
if diff < -7 or diff > 25:
    continue  # マッチしない
```


## 重複防止

時刻マッチングの許容窓（-7〜+25分）内で5分ポーリングが複数回走るため、
同じスケジュールに**複数回マッチし得る**。これを1回に絞る仕組み。

### 仕組み

`portfolio_config.monitor_last_runs` に、ラベルごとの最終実行時刻を記録する。

```json
{
  "US_AM": "2026-03-01T15:02:00+00:00",
  "JP_AM": "2026-03-01T01:12:00+00:00"
}
```

マッチ判定時に「同じラベルの最終実行が40分以内なら**スキップ**」する。
（許容窓が最大32分間あるため、それより長い40分に設定している）

### 動作例

`US_AM`（15:00 UTC）の場合:

```
14:57  cron発火 → diff = -3  → マッチ → Monitor実行 → last_run記録
15:02  cron発火 → diff = +2  → マッチ → last_runが5分前 → スキップ
15:07  cron発火 → diff = +7  → マッチ → last_runが10分前 → スキップ
  ...
15:27  cron発火 → diff = +27 → マッチしない（>25）→ 終了
```


## 関連ファイル

| ファイル | 役割 |
|----------|------|
| `shared/supabase_client.py` | `get_due_regular_schedules()`, `mark_regular_schedule_run()` |
| `Monitor/src/event_watch_check.py` | ポーリング時の判定スクリプト本体 |
| `.github/workflows/event-monitor.yml` | 5分間隔 cron の定義 |
| `config/portfolio_config.yml` | スケジュール定義（YAML） |


## 設定変更の方法

スケジュールの追加・変更・削除は `config/portfolio_config.yml` の
`monitor_schedules` セクションを編集して push するだけ。
詳細は [config-management.md](config-management.md) を参照。

全スケジュールを一時停止したい場合は `monitor_schedule_enabled: false` に変更する。
