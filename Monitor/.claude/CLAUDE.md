# CLAUDE.md


## プロジェクト概要

watchlist に登録された銘柄について、既存の投資プランの前提が
現在の市場状況で維持されているかを定期的にチェックするシステム。

2層アーキテクチャ:
- オーケストレーター（src/monitor_orchestrator.py）: フロー制御・DB操作。LLM不使用
- サブエージェント（.claude/commands/monitor-checker.md）: WebSearchで市場調査 + OK/NG判定


## 実行方法

```bash
python src/ng_dispatch.py                    # 監視 → NG銘柄は自動でDiscussion→Planning
python src/ng_dispatch.py --ticker NVDA      # 特定銘柄のみ
python src/ng_dispatch.py --monitor-only     # 監視のみ（Discussion/Planning起動しない）
python src/monitor_orchestrator.py           # 監視のみ（単体実行）
```


## 重要ファイル

| ファイル | 役割 |
|----------|------|
| src/ng_dispatch.py | パイプライン（Monitor → Discussion → Planning） |
| src/monitor_orchestrator.py | 監視オーケストレーター |
| src/AgentUtil.py | Claude Agent SDK ユーティリティ（Discussionからコピー） |
| .claude/commands/monitor-checker.md | 監視チェック用サブエージェント定義 |
