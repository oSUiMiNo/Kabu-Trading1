# pg_cron によるスケジュール発火（GitHub Actions の遅延対策）


## 用語解説

| 用語 | 説明 |
|------|------|
| **pg_cron** | Postgres データベースに内蔵されたタイマー機能。「毎日9時にこの処理を実行して」と登録できる。PCのアラームのDB版 |
| **pg_net** | Postgres から外部にHTTPリクエスト（ネット通信）を飛ばせる拡張機能。DBから直接 GitHub に「ワークフロー実行して」と命令できる |
| **cron** | 「何分ごと」「毎週月曜の9時」など、繰り返しスケジュールを定義する書式 |
| **SQL** | データベースに命令を出す言語 |
| **Vault** | Supabase の金庫機能。パスワードやAPIキーを暗号化して保管する |
| **PAT** | GitHub Personal Access Token。GitHub の API を外部から叩くための認証キー |
| **beta** | 「開発中」の意味。pg_net の関数名や仕様が将来変わる可能性がある |
| **cron.job_run_details** | pg_cron が実行記録を貯めるテーブル。定期的に掃除が必要 |
| **Fast Reboot** | Supabase ダッシュボードからDBを再起動する操作。pg_cron のプロセスが死んだ時の復旧手段 |
| **Management API** | Supabase を外部から操作するAPI。Fast Reboot をプログラムから実行できる |
| **GitHub Actions** | GitHub が提供するクラウド上の実行環境。Python スクリプトや AI エージェントを動かす場所。pg_cron はタイマーとしてこの環境を起動するだけで、実際の監視処理は全て GitHub Actions 上で走る |
| **workflow_dispatch** | GitHub Actions を外部から「今すぐ起動して」と命令できる仕組み。通常 GitHub Actions は cron（定期）や push（コード変更）で起動するが、workflow_dispatch を使えば API リクエスト1つで即座に起動できる。pg_net がこの API を叩く |
| **event_watch_check.py** | 「今実行すべきスケジュールがあるか？」を DB に問い合わせる軽量チェッカースクリプト。pg_cron 経由でも GH Actions cron 経由でも、必ずこのスクリプトが最初に走る。マッチするスケジュールがあれば後続の Monitor パイプラインを起動する |


## なぜこの仕組みが必要か

### 今までの問題

GitHub Actions の cron（定期実行）は「5分ごとに実行」と設定しても、
実際には **5分〜最大78分** のばらつきがある。
GitHub のサーバーが混んでいると後回しにされるため。

そのため、「10:10 に JP 株をチェックしたい」と思っても、
10:15 に来たり、11:00 過ぎに来たりする。

### 解決策

Supabase のデータベースに内蔵されている pg_cron で、
**狙った時刻ぴったりに** GitHub Actions を起動する。

ただし pg_cron も100%ではない（後述）ので、
既存の GitHub Actions 5分ポーリングをフォールバック（予備）として残し、
**二重化** で信頼性を確保する。


## 仕組みの全体像

```
┌─────────────────────────────────────────────────────────────┐
│  Supabase（クラウドDB）                                      │
│                                                             │
│  ┌─────────────────────────────────────────────┐            │
│  │ pg_cron（タイマー）                           │            │
│  │  「1:10 になった！」                          │            │
│  └──────────────┬──────────────────────────────┘            │
│                 ↓                                           │
│  ┌─────────────────────────────────────────────┐            │
│  │ pg_net（通信機能）                            │            │
│  │  GitHub API に「ワークフロー起動して」と送信    │            │
│  └──────────────┬──────────────────────────────┘            │
│                 │                                           │
│  ※ ここまでが Supabase 内の処理                               │
│  ※ タイマーと通信だけ。監視の実行能力はない                      │
└─────────────────┼───────────────────────────────────────────┘
                  │ workflow_dispatch（HTTP リクエスト）
                  ↓
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions（クラウド実行環境）                            │
│                                                             │
│  ┌─────────────────────────────────────────────┐            │
│  │ event_watch_check.py（チェッカー）             │            │
│  │  DB に問い合わせ「今マッチするスケジュールある？」│            │
│  │  → マッチ判定（-3分/+90分）                    │←── DB参照 ─┤
│  │  → 重複チェック（monitor_last_runs）           │            │
│  │  → pg_cron 死活監視                          │            │
│  └──────────────┬──────────────────────────────┘            │
│                 ↓ マッチあり                                  │
│  ┌─────────────────────────────────────────────┐            │
│  │ Monitor → Discussion → Planning              │            │
│  │  AI エージェントが市場を監視・分析・計画策定     │            │
│  │  結果を Discord に通知                        │            │
│  └─────────────────────────────────────────────┘            │
│                                                             │
│  ※ Python スクリプト、AI エージェント、                        │
│    WebSearch 等の実処理は全てここで動く                         │
└─────────────────────────────────────────────────────────────┘
```

```
【メイン経路】正確な時刻に発火

  Supabase: pg_cron → pg_net ──→ GitHub Actions: event_watch_check.py → Monitor
            (タイマー)  (通信)     (workflow_dispatch)  (チェック)         (実行)

【フォールバック経路】pg_cron が死んだ時の保険

  GitHub Actions: cron（5分ごと） → event_watch_check.py → Monitor
                  (GitHub自身の     (チェック)              (実行)
                   タイマー機能)

【自動復旧】pg_cron が死んだのを検知して直す

  GitHub Actions: event_watch_check.py ──→ Supabase: Management API で再起動
                  (毎回ヘルスチェック)        (Fast Reboot)
```


## pg_cron に登録されているジョブ一覧

全てのジョブは **Supabase DB 内**（クラウド）で実行される。所有者はすべて Supabase DB。ユーザーの PC には一切負荷がかからない。

### 本番スケジュール（時刻ぴったりに発火）

| ジョブ名 | cron 式 | 発火時刻 (UTC) | 対応スケジュール |
|----------|---------|---------------|-----------------|
| `monitor_JP_AM` | `10 1 * * 1,2,3,4,5` | 月〜金 01:10 | JP_AM（10:10 JST） |
| `monitor_JP_PM` | `0 7 * * 1,2,3,4,5` | 月〜金 07:00 | JP_PM（16:00 JST） |
| `monitor_US_AM` | `0 15 * * 0,1,2,3,4` | 日〜木 15:00 | US_AM（0:00 JST） |
| `monitor_US_PM` | `30 21 * * 0,1,2,3,4` | 日〜木 21:30 | US_PM（6:30 JST） |
| `monitor_WEEKEND` | `0 1 * * 0,6` | 土日 01:00 | WEEKEND（10:00 JST） |

### テスト用スケジュール（毎分チェック）

| ジョブ名 | cron 式 | 動作 |
|----------|---------|------|
| `fire_test_schedules` | `* * * * *` | 毎分、DB に `test: true` のスケジュールがあり未発火（10分以内に実行なし）なら workflow_dispatch |

テストスケジュールを DB に追加すれば **最大1分以内** に発火し、**発火後に自動削除** される（1回限り）。
テストスケジュールがなければ空振り（SELECT 1回のみ、負荷は実質ゼロ）。

### 保守ジョブ

| ジョブ名 | cron 式 | 動作 |
|----------|---------|------|
| `retry_failed_dispatches` | `*/5 * * * *` | 5分ごと、直近30分の失敗ジョブがあれば workflow_dispatch をリトライ |
| `cleanup_job_run_details` | `0 4 * * *` | 毎日 04:00 UTC、7日より古い実行記録を削除（ストレージ節約） |


## DB 上の SQL 関数

すべて **Supabase DB 内**（クラウド）で定義・実行される SQL 関数。

| 関数名 | 所有者 | 役割 |
|--------|--------|------|
| `trigger_workflow_dispatch()` | Supabase DB | Vault から PAT を取得し、pg_net で GitHub API の workflow_dispatch を呼ぶ。pg_net をラップしており、beta の仕様変更時にここだけ修正すれば済む |
| `fire_test_schedules()` | Supabase DB | `portfolio_config.monitor_schedules` から `test: true` のエントリを探し、`monitor_last_runs` で10分以内に実行済みでなければ `trigger_workflow_dispatch()` を呼び、**発火後にそのエントリを自動削除**する（1回限りの発火） |
| `retry_failed_dispatches()` | Supabase DB | `cron.job_run_details` で直近30分の失敗を検索し、あれば `trigger_workflow_dispatch()` を再実行 |
| `check_pg_cron_health()` | Supabase DB | pg_cron の monitor ジョブの最終成功時刻、経過時間、失敗数を返す。`event_watch_check.py` から RPC で呼ばれる |


## 実行済み判定の仕組み（2段階の重複チェック）

重複チェックは **2箇所** にあり、両方とも DB の同じカラム `portfolio_config.monitor_last_runs` を参照する。

```json
{
  "JP_AM": "2026-03-02T01:10:15+00:00",
  "TEST": "2026-03-02T05:01:15+00:00"
}
```

### チェックポイント1：Supabase DB 内（workflow_dispatch を飛ばす前）

| 関数 | 場所 | 何をするか |
|------|------|-----------|
| `fire_test_schedules()` | Supabase DB 内の SQL 関数 | `test: true` のスケジュールについて `monitor_last_runs` を確認。10分以内に実行済みなら workflow_dispatch を飛ばさない |

※ 本番スケジュール用の pg_cron ジョブ（`monitor_JP_AM` 等）はここを通らない。
決まった時刻に1回だけ `trigger_workflow_dispatch()` を呼ぶだけ。

**役割**：無駄な workflow_dispatch の発行を防ぐ（GitHub Actions の起動コストを節約）。

### チェックポイント2：GitHub Actions 上（Monitor を実行する前）

| 関数 | 場所 | 何をするか |
|------|------|-----------|
| `get_due_regular_schedules()` | `shared/supabase_client.py`（GitHub Actions 上で実行） | 全スケジュールについて `monitor_last_runs` を確認。本番は150分、テストは10分以内に実行済みならスキップ |

**役割**：pg_cron と GH Actions cron の両方が同じスケジュールを拾った場合の二重実行を防ぐ。

### 2段階チェックの流れ

```
【テストスケジュールの場合】

  fire_test_schedules()（Supabase DB、毎分）
    ├─ チェック1: last_runs で10分以内に実行済み？ → YES → 何もしない
    └─ NO → trigger_workflow_dispatch()
              → GitHub Actions 起動
              → event_watch_check.py
              → get_due_regular_schedules()
                ├─ チェック2: last_runs で10分以内に実行済み？ → YES → スキップ
                └─ NO → Monitor 実行 → last_runs 更新


【本番スケジュールの場合】

  pg_cron monitor_JP_AM（Supabase DB、1:10 UTC に1回）
    └─ チェック1 なし → trigger_workflow_dispatch()
              → GitHub Actions 起動
              → event_watch_check.py
              → get_due_regular_schedules()
                ├─ チェック2: last_runs で150分以内に実行済み？ → YES → スキップ
                └─ NO → Monitor 実行 → last_runs 更新

  GH Actions 5分ポーリング（1:12 UTC に来た場合）
    └─ event_watch_check.py
       → get_due_regular_schedules()
         └─ チェック2: last_runs → 2分前に実行済み → スキップ
```

### なぜ2箇所で重複チェックするのか

チェック1（DB内）だけだと：
- GH Actions の5分ポーリングはチェック1を通らないため、重複を防げない

チェック2（GH Actions上）だけだと：
- `fire_test_schedules()` が毎分 workflow_dispatch を飛ばし続け、
  GitHub Actions が毎分起動するムダが発生する（Monitor は実行されないが Actions の起動コストがかかる）

両方あることで、無駄な Actions 起動を防ぎつつ、全経路での二重実行も防いでいる。


## なぜ二重化するのか

pg_cron と GitHub Actions cron は **別の壊れ方をする**。

| | pg_cron | GitHub Actions cron |
|--|---------|-------------------|
| タイミング精度 | 分単位で正確（通常1秒未満） | 5〜78分のばらつき |
| 壊れ方 | スケジューラープロセスが死んで**全停止** | 高負荷時に**遅延・スキップ** |
| 頻度 | まれ | 常態的 |

片方だけだとどちらの壊れ方でも止まる。両方あれば：
- **pg_cron が正常** → 正確な時刻に発火。GH Actions は monitor_last_runs で弾かれる
- **pg_cron が死亡** → GH Actions の5分ポーリングが拾う。自動リブートで pg_cron 復旧
- **GH Actions が遅延** → pg_cron が先に発火済み。問題なし
- **両方死亡** → 現実的にはほぼ起きない（独立した障害）


## 二重発火しない仕組み

既存の `monitor_last_runs`（重複防止150分）がそのまま機能する。

```
例: JP_AM スケジュール（10:10 JST = 1:10 UTC）

1:10 UTC  pg_cron → workflow_dispatch → event_watch_check.py
            → JP_AM マッチ → Monitor実行 → last_runs に "JP_AM: 1:10" を記録

1:12 UTC  GH Actions cron → event_watch_check.py
            → JP_AM マッチ → last_runs を見る → 2分前に実行済み → スキップ
```


## 2つの経路でマッチ判定の効き方が異なる理由

pg_cron 経由でも GH Actions 経由でも、最終的に同じ
`get_due_regular_schedules()` を通り、同じ許容幅（前方 -3分 / 後方 +90分）で判定される。
コード上の扱いに違いはない。

違いが生まれるのは **判定に到達する時刻** である。

```
【pg_cron 経路】

  pg_cron: 1:10 UTC ぴったりに発火
       → workflow_dispatch → GitHub Actions 起動（数秒〜1分）
       → event_watch_check.py 実行
       → get_due_regular_schedules(now=1:10)
       → diff = 0分 → マッチ ✓

  → 通常は diff ≒ 0 なのでマージンに頼らずマッチする。
    万が一 pg_cron が数分遅れた場合でもマージン内なので問題なく拾われる。

【GH Actions cron 経路】

  GH Actions cron: 5分間隔で event_watch_check.py を実行
       → たまたま 1:07 に来た場合
       → get_due_regular_schedules(now=1:07)
       → diff = -3分 → 前方マージン内 → マッチ ✓
       → pg_cron より3分早く実行されてしまう

  → 前方マージン（-3分）が効くのはこの経路のみ。
    pg_cron の正確な発火を GH Actions が先取りする可能性がある。
    ただし市場監視として3分の差は実害なし。

  GH Actions が先に実行した場合も、monitor_last_runs に記録されるため、
  1:10 に pg_cron が発火しても重複実行にはならない（last_runs で弾かれる）。
```

### 後方マージン（+90分）の役割

後方マージンは **pg_cron 死亡時のフォールバック** のためにある。
pg_cron が正常な場合は diff ≒ 0 なので使われない。

pg_cron が停止し、GH Actions だけが動いている場合：
- GH Actions の cron は最大78分遅延しうる
- +90分の後方マージンがこの遅延をカバーする


## pg_cron の既知のリスク

pg_cron は GitHub Actions cron より精度は高いが、以下のリスクがある。

| リスク | 影響 | 対策 |
|--------|------|------|
| スケジューラープロセスの死亡 | 全ジョブ停止 | GH Actions フォールバック + 自動 Fast Reboot |
| pg_net 通信失敗（成功率 ~95%） | 発火が届かない | 失敗検知ジョブで5分ごとリトライ + GH Actions フォールバック |
| pg_net が beta（仕様変更の可能性） | 将来壊れる可能性 | `trigger_workflow_dispatch()` でラップして変更箇所を1箇所に限定 |
| cron.job_run_details の肥大化 | ストレージ圧迫 | `cleanup_job_run_details` ジョブが毎日7日超の記録を削除 |
| Free tier の7日ポーズ | DB全停止 | GH Actions の5分ポーリングが DB にアクセスするため実質発生しない |


## 関連する場所

| 場所 | 所有者 | 何があるか |
|------|--------|-----------|
| Supabase DB 内 | Supabase DB | pg_cron ジョブ（7個）、SQL 関数（4個）、Vault（PAT 保管） |
| `portfolio_config.monitor_schedules` | Supabase DB | スケジュール定義（今まで通り） |
| `portfolio_config.monitor_last_runs` | Supabase DB | 実行済み判定（ラベルごとの最終実行時刻） |
| `.github/workflows/event-monitor.yml` | EventScheduler | Monitor ワークフロー（pg_cron からも cron からも起動される） |
| `shared/supabase_client.py` の `get_due_regular_schedules()` | shared | スケジュールマッチング + 重複防止判定 |
| `Monitor/src/event_watch_check.py` | Monitor | スケジュールチェック + pg_cron 死活監視 + 自動 Fast Reboot + パイプライン起動 |


## セキュリティ

| 秘密情報 | 保管場所 | 用途 |
|----------|---------|------|
| GitHub PAT | Supabase Vault（暗号化） | pg_net から GitHub API を叩く |
| Supabase Management API トークン | GitHub Secrets | GH Actions から Fast Reboot を実行 |

GitHub PAT を平文でDBに置くと漏洩リスクがあるため、
**Vault（金庫）** に暗号化して保管し、使うときだけ復号して使う。


## 安全装置一覧

| 装置 | 何をするか |
|------|-----------|
| **GH Actions フォールバック** | pg_cron が壊れても既存の5分ポーリングがスケジュールを拾う |
| **pg_cron 死活監視** | event_watch_check.py が毎回 `check_pg_cron_health()` を呼び、24時間成功なしで自動 Fast Reboot |
| **失敗検知+リトライジョブ** | `retry_failed_dispatches` が5分ごとに pg_net の通信失敗をチェック。失敗していたらリトライ |
| **テスト即時発火** | `fire_test_schedules` が毎分チェック。`test: true` のスケジュールを最大1分以内に発火 |
| **関数ラップ** | pg_net を直接呼ばず `trigger_workflow_dispatch()` で包む。beta の仕様変更時に1箇所修正で済む |
| **定期クリーンアップ** | `cleanup_job_run_details` が毎日7日超の記録を削除（Free tier 500MB 上限対策） |
| **重複防止** | `monitor_last_runs` で pg_cron 経由でも cron 経由でも二重実行しない |
| **Discord 通知** | pg_cron 停止検知・リブート実行時に通知 |
| **初期状態保護** | まだ1度も発火していない状態では Fast Reboot を実行しない |
