# 投資パイプライン（Kabu-Trading1）

テクニカル指標取得 → 銘柄の監視 → 議論 → プラン生成 → watchlist 更新を自動化するシステム。
Claude Code / GLM のマルチエージェント構成で運用する。

---

## システム全体像

```
定期ルート:  [Technical] → [ImportantIndicators] → [IndicatorFilter] → [Monitor] → [Analyzer] → [Planning] → [Watch] → [ActionLog]
                                                                          ↑                                                   |
                                                                          └──────────────── archive テーブル ←─────────────────┘

手動ルート:  [Admission] → [Technical] → [ImportantIndicators] → [Analyzer] → [Planning] → [Watch] → [ActionLog]
             (Monitor / IndicatorFilter をスキップ)
```

1. **Technical** が watchlist 全銘柄のテクニカル指標を取得し archive に記録
2. **ImportantIndicators** が市場全体データ（VIX・金利等）+ 個別銘柄データ（決算・相対強度等）を取得し archive に記録
3. **IndicatorFilter** がテクニカル・重要指標の変化を決定論的に判定し、Monitor 対象銘柄を絞り込む
4. **Monitor** がテクニカル・重要指標データを参照しつつ現状をチェックし OK/NG/ERROR を判定
5. **Analyzer** が NG 銘柄を複数エージェントで議論し最終判定を出す
6. **Planning** が最終判定からプラン YAML を生成
7. **Watch** がプラン結果をもとに watchlist を更新し、Discord に業務通知を送信
8. **ActionLog** が action_log テーブルへの記録投入と holdings の同期を実行

全ブロックが同一の archive レコードに順番に書き足す。ブロックごとに別レコードを作らない。

ブロック間の情報伝達は **DB（archive テーブル）経由の伝言板方式** で疎結合。環境変数・引数・ファイル等による大ブロック間の直接的なデータ受け渡しは禁止。
ブロック内のサブエージェント間は変数・引数で直接渡す、またはファイルを作成してファイルパスを渡す方式で連携する。
各ブロック内では銘柄ごとに `asyncio.gather()` で並列処理する。

---

## モジュール構成

| モジュール | 役割 | 主要ファイル |
|-----------|------|------------|
| **Technical** | テクニカル指標取得 + archive 作成 | `technical_batch.py`<br>`Technical/src/main.py` |
| **ImportantIndicators** | 市場全体・個別銘柄の重要指標取得 | `importantindicators_batch.py`<br>`ImportantIndicators/src/main.py` |
| **IndicatorFilter** | Monitor 実行要否の決定論的判定 | `shared/indicator_filter.py` |
| **Monitor** | 市場チェック + パイプライン制御 | `monitor_batch.py`<br>`Monitor/src/main.py` |
| **Analyzer** | 複数レーン並列議論 + 最終判定 | `analyzer_batch.py`<br>`Analyzer/src/main.py` |
| **Planning** | プラン YAML 生成 | `planning_batch.py`<br>`Planning/src/main.py` |
| **Watch** | watchlist 更新 + Discord 業務通知 | `watch_batch.py`<br>`Watch/src/main.py` |
| **ActionLog** | action_log 投入 + holdings 同期（Phase 6） | `actionlog_batch.py`<br>`ActionLog/src/pipeline_main.py` |
| **EventScheduler** | 経済イベント取得・DB登録 | `EventScheduler/src/main.py` |
| **NightWorker** | archive 品質レビュー + Issue 作成 + 用語集統合 | `NightWorker/src/main.py`<br>`NightWorker/src/words_consolidator.py` |
| **ActionLog** | 月次アクションログ表示 Web UI（NiceGUI） | `ActionLog/src/main.py` |
| **ManualEntry** | 手動銘柄入力による分析パイプライン | `manual_pipeline.py` |
| **shared** | DB・通知・LLM クライアント共通 | `shared/supabase_client.py`<br>`shared/discord_notifier.py`<br>`shared/llm_client.py` |

---

## 実行方法

```bash
# 全パイプライン（Technical → Monitor → Analyzer → Planning → Watch）
python main_pipeline.py

# 特定銘柄のみ
python main_pipeline.py --ticker NVDA

# 監視のみ（Analyzer/Planning/Watch 起動しない）
python main_pipeline.py --monitor-only

# 米国株のみ
python main_pipeline.py --market US

# 監視バッチ（単体実行）
python monitor_batch.py

# 手動銘柄入力（GitHub Actions 経由・Monitor をスキップして直接分析）
gh workflow run manual-analysis.yml -f ticker=NVDA
gh workflow run manual-analysis.yml -f ticker=3038 -f market=JP -f display_name=神戸物産
```

---

## 自動実行（GitHub Actions）

| ワークフロー | スケジュール | 内容 |
|-------------|------------|------|
| `event-monitor.yml` | 5分間隔 24時間稼働 | イベント watch + 定期スケジュール検出 → パイプライン起動 |
| `monitor.yml` | 手動（workflow_dispatch） | テスト・特定銘柄の手動チェック |
| `event-scheduler.yml` | 年1回・月1回 | FOMC・雇用統計等の経済イベント取得・DB登録 |
| `night-worker.yml` | JST 3:05 / 4:05 / 5:05 | archive 品質レビュー → GitHub Issue 作成 |
| `sync-config.yml` | push 時自動 | `config/portfolio_config.yml` → DB 反映 |

### 定期 Monitor スケジュール（pg_cron）

| ジョブ | 発火時刻（UTC） | 対象（JST） |
|--------|---------------|------------|
| monitor_JP_AM | 月〜金 01:10 | 日本株 10:10 |
| monitor_JP_PM | 月〜金 07:00 | 日本株 16:00 |
| monitor_US_AM | 日〜木 15:00 | 米国株 0:00 |
| monitor_US_PM | 日〜木 21:30 | 米国株 6:30 |

メイン経路は pg_cron → pg_net → GitHub Actions workflow_dispatch。
フォールバックとして GitHub Actions 5分ポーリングが二重化カバーする。

---

## Discord 通知

| ラベル | 送信元 | 条件 |
|--------|--------|------|
| 開始 | main_pipeline | パイプライン開始時 |
| 確認 | main_pipeline | OK だがリスクフラグあり |
| 緊急 | Watch | NG かつ変動率 ≤ -10% |
| 朗報 | Watch | NG かつ変動率 ≥ +10% |
| 警告 | Watch | NG（変動率 -10%〜+10%） |
| 完了 | main_pipeline | 全銘柄チェック完了時 |
| エラー | main_pipeline | リトライ上限到達 / 失敗 |

---

## Analyzer の LLM バックエンド

Analyzer ブロックは Claude Code と Z.AI GLM の2つのバックエンドに対応している。
`.env.local` の環境変数で切り替える。

```env
# バックエンド選択（claude / glm）
ANALYZER_LLM_PROVIDER=glm

# GLM モデル指定
ANALYZER_GLM_MODEL=glm-4.7
```

### Z.AI GLM の利用

| 項目 | 内容 |
|------|------|
| アカウント | osuimino@gmail.com |
| 現在のプラン | **Coding Plan Lite**（年間） |
| 現在のモデル | **GLM-4.7**（推論モデル、最大 128K コンテキスト） |
| API キー | `.env.local` の `ZHIPUAI_API_KEY` に設定済み |
| エンドポイント | `https://api.z.ai/api/coding/paas/v4/`（Coding Plan 用） |
| 切り替え方法 | `ANALYZER_GLM_MODEL` の値を変更するだけ（コード変更不要） |
| クォータ | 5時間あたり 80〜120 プロンプト / 週間上限 約400 |

Lite プランで利用可能なモデル：`glm-4.7`、`glm-4.7-flash`（無料）
GLM-5 を使うには Pro 以上へのアップグレードが必要

---

## 設定管理

`config/portfolio_config.yml` を編集して push すると、`sync-config.yml` ワークフロー経由で自動的に DB に反映される。

主要設定：
- 投資パラメータ（total_budget_jpy, risk_limit_pct, stop_loss_pct 等）
- Analyzer パラメータ（num_lanes, max_rounds 等）
- IndicatorFilter 閾値（filter_vix_threshold, filter_event_threshold_days 等）
- Monitor 定期スケジュール
- 緊急停止スイッチ（`monitor_schedule_enabled: false` で全スケジュール一時停止）
- フィルター無効化（`indicator_filter_enabled: false` でフィルターをバイパス）

---

## DB 構造（主要テーブル）

| テーブル | 用途 |
|---------|------|
| `archive` | パイプライン出力の蓄積（technical, monitor, lanes, final_judge, newplan_full, verdict） |
| `watchlist` | 監視対象銘柄と最新サマリー |
| `event_master` | 経済イベントのマスター |
| `event_watch` | イベント watch スケジュール |
| `portfolio_config` | 全設定値（YAML → DB 同期） |
| `archive_reviews` | archive 品質レビュー結果 |
| `holdings` | 保有銘柄情報（株数・平均取得単価・現在価格） |
| `action_log` | ActionLog アプリ用のアクション履歴 |
| `action_log_handoff` | ActionLog の月別引き継ぎ文キャッシュ |

### 伝言板方式の開始条件

| ブロック | DB クエリ条件 |
|---------|-------------|
| ImportantIndicators | `technical IS NOT NULL AND important_indicators IS NULL` |
| IndicatorFilter | オーケストレーター内で実行（DB 条件ではなくプログラム判定） |
| Monitor | `technical IS NOT NULL AND monitor IS NULL`（IndicatorFilter がトリガーした銘柄のみ） |
| Analyzer | `active = True AND final_judge IS NULL` |
| Planning | `active = True AND final_judge IS NOT NULL AND newplan_full IS NULL` |
| Watch | `active = True AND status = 'completed' AND newplan_full IS NOT NULL` |
| ActionLog | `active = False AND monitor IS NOT NULL AND archive.id が action_log に未登録` |

---

## ドキュメント

詳細な設計・運用ドキュメントは `MyDocs/` に集約している。

| ファイル | 内容 |
|---------|------|
| パイプライン総則.md | ブロック間・ブロック内の構造原則 |
| ブロック別データフロー.md | 各ブロックの入力・処理・出力の一覧 |
| 新アーキテクチャ設計.md | 現行アーキテクチャの設計書 |
| Discord通知.md | 通知ラベルの種類・色・送信タイミング |
| リスクフラグ.md | monitor-checker が付与するタグ定義 |
| 各ブロック不足入力情報.md | 各ブロックに渡すべき追加情報の設計 |
| pgcronトリガー.md | pg_cron + pg_net による二重化トリガー |
| 設定管理.md | portfolio_config テーブルの管理方法 |
| 指標フィルター設計.md | IndicatorFilter の仕様・閾値・実装ステップ |
