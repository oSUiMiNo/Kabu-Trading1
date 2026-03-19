# CLAUDE.md


## プロジェクト概要

watchlist に登録された銘柄について、既存の投資プランの前提が
現在の市場状況で維持されているかを定期的にチェックするシステム。

2層アーキテクチャ:
- オーケストレーター（src/main.py）: フロー制御・DB操作。LLM不使用
- サブエージェント（.claude/commands/monitor-checker.md）: WebSearchで市場調査 + OK/NG判定


## 実行方法

```bash
python main_pipeline.py                    # 全パイプライン（Monitor → Discussion → Planning → Watch）
python main_pipeline.py --ticker NVDA      # 特定銘柄のみ
python main_pipeline.py --monitor-only     # 監視のみ（Discussion/Planning/Watch 起動しない）
python main_pipeline.py --market US        # 米国株のみ
python Monitor/src/main.py # 監視のみ（単体実行）
```


## 重要ファイル

| ファイル | 役割 |
|----------|------|
| (PJTルート) main_pipeline.py | パイプラインオーケストレーター（Monitor → Discussion → Planning → Watch） |
| src/main.py | 監視オーケストレーター |
| src/event_watch_check.py | イベント watch + 定期スケジュールの検出（event-monitor.yml から呼ばれる） |
| src/AgentUtil.py | Claude Agent SDK ユーティリティ（shared/agent_util.py のラッパー） |
| .claude/commands/monitor-checker.md | 監視チェック用サブエージェント定義 |


## 自動実行

定期 Monitor は `event-monitor.yml`（5分ポーリング）に統合済み。
`event_watch_check.py` がイベント watch と定期スケジュール（`portfolio_config.monitor_schedules`）の
両方を検出し、マッチしたら `main_pipeline.py` を起動する。

`monitor.yml` は手動実行（`workflow_dispatch`）専用。

設定の詳細は `MyDocs/config-management.md` を参照。
