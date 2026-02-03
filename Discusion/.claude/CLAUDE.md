# プロジェクト概要

株の銘柄評価を「考察（Analyst）」と「反対側固定（Devil's Advocate）」の対立議論形式で行う。
Analystが結論を出し、Devil's AdvocateはそのstanceのRound中は**常に反対側**に立って反論Claimsを組み立てる。
議論は `logs/` に筆談ログとして蓄積し、機械処理しやすいEXPORTを各Round末尾に出力する。

## 共通ルール（最重要）
共通ルールは **skill に一本化**：
- `.claude/skills/stock-log-protocol/SKILL.md`

ログのID体系、S#フォーマット（type/retrieved_at必須）、鮮度の目安、Round末尾の暫定結論/EXPORTはすべてskillに従う。

## サブエージェント
- `.claude/agents/analyst.md`
- `.claude/agents/devils-advocate.md`

両サブエージェントは frontmatter の `skills:` で `stock-log-protocol` をロードして運用する。
