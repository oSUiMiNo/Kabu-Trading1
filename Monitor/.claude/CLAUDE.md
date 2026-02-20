# CLAUDE.md


## プロジェクト概要

watchlist に登録された銘柄について、既存の投資プランの前提が
現在の市場状況で維持されているかを定期的にチェックするシステム。

2層アーキテクチャ:
- オーケストレーター（src/monitor_orchestrator.py）: フロー制御・DB操作。LLM不使用
- サブエージェント（.claude/commands/monitor-checker.md）: WebSearchで市場調査 + OK/NG判定


## 実行方法

```bash
python src/monitor_orchestrator.py              # watchlist 全銘柄チェック
python src/monitor_orchestrator.py --ticker NVDA # 特定銘柄のみ
```


## 重要ファイル

| ファイル | 役割 |
|----------|------|
| src/monitor_orchestrator.py | エントリーポイント + フロー制御 |
| src/AgentUtil.py | Claude Agent SDK ユーティリティ（Discussionからコピー） |
| .claude/commands/monitor-checker.md | 監視チェック用サブエージェント定義 |
