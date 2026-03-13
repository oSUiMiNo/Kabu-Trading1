# CLAUDE.md

## プロジェクト概要

archive テーブルに蓄積されたパイプライン出力（Discussion/Planning）の品質を
自動レビューし、問題があれば GitHub Issue を作成するモジュール。

深夜帯（JST 3〜5時）に GitHub Actions で定期実行される。

## 重要ファイル

| ファイル | 役割 |
|----------|------|
| src/review_orchestrator.py | メインオーケストレーター |
| src/issue_creator.py | GitHub Issue 作成ロジック |
| .claude/commands/archive-reviewer.md | レビュー用サブエージェント |
