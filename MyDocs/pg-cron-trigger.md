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
【メイン経路】pg_cron → pg_net → GitHub Actions

  pg_cron（DB内蔵タイマー）
       │  スケジュール時刻ぴったりに発火
       ↓
  pg_net（DB内蔵の通信機能）
       │  GitHub API に「ワークフローを実行して」とリクエスト
       ↓
  GitHub Actions（workflow_dispatch で起動）
       │  event_watch_check.py → Monitor パイプライン
       ↓
  Monitor → Discussion → Planning


【フォールバック経路】GitHub Actions cron（今まで通り）

  GitHub Actions の cron → 5分ごとに event_watch_check.py を起動
       │  「今マッチするスケジュールある？」をDBに聞く
       ↓
  マッチすれば Monitor パイプラインを実行
  （pg_cron が先に実行済みなら monitor_last_runs で弾かれる）


【自動復旧】pg_cron 停止の検知と修復

  event_watch_check.py（GH Actions から毎回実行）
       │  cron.job_run_details を確認
       │  「pg_cron の最終成功が24時間以上前？」
       ↓
  24時間以上 → Supabase Management API で自動 Fast Reboot
             → Discord に「pg_cron 停止 → 自動リブート」通知
```


## なぜ二重化するのか

pg_cron と GitHub Actions cron は **別の壊れ方をする**。

| | pg_cron | GitHub Actions cron |
|--|---------|-------------------|
| タイミング精度 | 分単位で正確 | 5〜78分のばらつき |
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


## pg_cron の既知のリスク

pg_cron は GitHub Actions cron より精度は高いが、以下のリスクがある。

| リスク | 影響 | 対策 |
|--------|------|------|
| スケジューラープロセスの死亡 | 全ジョブ停止 | GH Actions フォールバック + 自動 Fast Reboot |
| pg_net 通信失敗（成功率 ~95%） | 発火が届かない | 失敗検知ジョブでリトライ + GH Actions フォールバック |
| pg_net が beta（仕様変更の可能性） | 将来壊れる可能性 | 関数でラップして変更箇所を1箇所に限定 |
| cron.job_run_details の肥大化 | ストレージ圧迫 | 定期クリーンアップジョブで古い記録を削除 |
| Free tier の7日ポーズ | DB全停止 | GH Actions の5分ポーリングが DB にアクセスするため実質発生しない |


## 関連する場所

| 場所 | 何があるか |
|------|-----------|
| Supabase DB 内 | pg_cron ジョブ、pg_net 通信関数、Vault（トークン保管庫） |
| `portfolio_config.monitor_schedules` | スケジュール定義（今まで通り） |
| `.github/workflows/event-monitor.yml` | Monitor ワークフロー（pg_cron からも cron からも起動される） |
| `shared/supabase_client.py` | スケジュールマッチング処理（今まで通り） |
| `Monitor/src/event_watch_check.py` | スケジュールチェック + pg_cron 死活監視 + パイプライン起動 |


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
| **pg_cron 死活監視** | event_watch_check.py が毎回 cron.job_run_details を確認。24時間成功なしで自動 Fast Reboot |
| **失敗検知+リトライジョブ** | pg_net の通信失敗を定期チェック。失敗していたらリトライ |
| **関数ラップ** | pg_net を直接呼ばず関数で包む。beta の仕様変更時に1箇所修正で済む |
| **定期クリーンアップ** | cron.job_run_details の古い記録を定期削除（ストレージ節約） |
| **重複防止** | monitor_last_runs で pg_cron 経由でも cron 経由でも二重実行しない |
| **Discord 通知** | pg_cron 停止検知・リブート実行時に通知 |
