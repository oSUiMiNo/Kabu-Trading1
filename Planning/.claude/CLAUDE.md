# CLAUDE.md


## プロジェクト概要

Discussionプロジェクトのログ一式（議論・判定・最終判定）を受け取り、
予算・リスク制約を反映した実行可能なプラン（PlanSpec）を YAML で出力するシステム。

2層アーキテクチャ:
- オーケストレーター（src/*.py）: フロー制御・決定論的計算。LLM不使用
- サブエージェント（.claude/commands/*.md）: commentary テキスト生成のみ


## 実行方法

```bash
python src/plan_orchestrator.py <銘柄> <セッションDir> <予算(円)> <リスク上限> <期間> <現在価格> <基準価格>
```


## 重要ファイル

| ファイル | 役割 |
|----------|------|
| src/plan_orchestrator.py | エントリーポイント + フロー制御 |
| src/plan_calc.py | 全固定ルールの計算（鮮度/価格ズレ/配分/株数） |
| src/log_parser.py | Discussionログ解析 |
| src/plan_spec.py | PlanSpecデータ構造 + YAML生成 |
| src/AgentUtil.py | Claude Agent SDK ユーティリティ（Discussionからコピー） |
| .claude/commands/plan-generator.md | サブエージェント定義 |
| youken.md | 要件定義書 |
