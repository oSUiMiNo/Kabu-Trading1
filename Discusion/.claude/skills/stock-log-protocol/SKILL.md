---
name: stock-log-protocol
description: 株銘柄の考察ログ（Analyst / Devil's Advocate）を同一フォーマット・同一ID体系・同一S#規約で運用するための共通ルール。
---

# Stock Log Protocol（共通ルール）

この skill は、`logs/` 配下の筆談ログを **誤読しにくい・機械処理しやすい**形式で維持するための共通ルール。
Analyst / Devil's Advocate の双方に適用する。

---

## 0. 大原則（追記運用）

- **過去のRound本文は削除しない**（追記のみ）
- グローバルな `## 結論` / `## EXPORT` は **運用しない**（あっても更新しない）
- 代わりに **各Roundの末尾**に必ず以下を付ける：
  - `### 暫定結論（as of YYYY-MM-DD HH:MM）`
  - `### EXPORT（yaml）`
- **オーケストレーターは「最後のEXPORT」だけ採用**する想定

---

## 1. ID体系（通し番号・Roundでリセット禁止）

| ID | 用途 | ルール |
|---|---|---|
| S# | Sources（参照元） | `type` と `retrieved_at` を必須 |
| F# | Facts（事実・観測） | **必ず S# を付与**（例: `F12[S3]`） |
| C# | Claims（主張・判断） | 根拠となる `F#` を明記 |
| H# | Hypotheses（仮説） | **ソース無し推測の避難場所**（F#に混ぜない） |
| Q# | Open Questions（未解決） | 不明点・要追加調査 |
| R# | Risks（重要リスク） | 重大な注意点 |
| A# | Actions（次アクション） | 次にやること |
| D# | Decisions（決定事項） | 確定方針（必要時のみ） |

### 1.1 ソース無し情報の扱い
- **S#が無い情報はF#にしない**
  - 推測 → `H#`
  - 未確認で調査が必要 → `Q#` に落として `A#` で調査
- `verified_facts` には **S#付きF#のみ**入れる（H#は入れない）

---

## 2. S#（Sources）フォーマット固定

`logs/` の先頭に `## Sources（参照元一覧）` を置き、S#を管理する。

### 2.1 必須カラム
| ID | type | ref | retrieved_at | notes |

- `type`（必須）: `price_metric` / `news` / `earnings` / `filing` / `research` / `other`
- `retrieved_at`（必須）: **ISO 8601 + TZ**（例: `2026-01-31T11:00+09:00`）
- `ref`（必須）: URL もしくは資料名（EDINET/TDnet/会社IRページ/記事URLなど）

### 2.2 "S#の再利用"ルール（同じソースは増やさない）
- `type` + `ref` が同一なら **同じ S# を再利用**（新しいS#を作らない）
- ただし鮮度更新が必要なら、**Sources表の当該行の `retrieved_at` を更新してよい**
  （Round本文は追記のみ、Sources表はメタデータとして更新OK）

---

## 3. 鮮度（Freshness）運用メモ（共通）

鮮度は常に最優先ではない（ノイズ/コスト増）。
ただし、結論が買い寄りに傾く・または株価/指標/ニュース/決算が根拠に入る場合は
`retrieved_at` を意識して可能な範囲で新しめを使う。

古い/不明なら **Q#/A# へ落とす**。

### 3.1 データ種別ごとの目安（トリガー成立時に厳守寄り）
- **株価/指標（price_metric）**：数日〜**1週間**以内が目安
- **ニュース（news）**：**1〜2週間**以内が目安
- **決算（earnings / filing）**：期間より **「最新四半期/最新の開示か」** を最重要
  （古い四半期しか無いなら Q#/A# で最新確認）

---

## 4. 数値Fact（F#）の最小要件（短くてOK）

数値をF#にする場合、最低限この3点は入れる：
- **単位**（円 / % / 倍 / 株 / 人 / USD など）
- **期間**（FY2025 / 2025Q3 / TTM / as_of日付 など）
- **母数/前提**（必要なときだけでOK：連結/単体、1株あたり、調整後、発行株式数ベース等）

例：
- `F12[S3]: 営業利益=123億円; period=2025Q3; basis=連結`
- `F13[S4]: PER=12.3x; as_of=2026-01-31; basis=TTM`

---

## 5. Roundの標準フォーマット（必須）

各Roundはこの形で追記する（Nはログ内の連番）：

```markdown
---

## Round N - <Analyst|Devil's Advocate>（YYYY-MM-DD HH:MM）

### サマリ
- [このRoundでやったことを短く]

### 本文
- [必要なら箇条書きで。IDを付与]

### 台帳ブロック
**Sources（追加/更新がある場合のみ）:**
- S#: （Sources表を更新した旨をメモしてもよい）

**Facts（追加/参照）:**
- F(通し番号)[S#]: ...

**Claims（追加/修正）:**
- C(通し番号): ...（根拠: F#, F#）
- （推測の避難が必要なら）H(通し番号): ...

**Open Questions:**
- Q(通し番号): ...

**Risks:**
- R(通し番号): ...

**Actions:**
- A(通し番号): ...

### 暫定結論（as of YYYY-MM-DD HH:MM）
※ **C#/R#/Q#/A#参照のみ**（ここで新規F#/C#を書かない）
- stance: [強気 / 条件付き / 見送り / 要追加調査]
- rationale: [C#, C#, ...]
- risks: [R#, ...]
- open_questions: [Q#, ...]
- next_actions: [A#, ...]

### EXPORT（yaml）
as_of: YYYY-MM-DDTHH:MM+09:00
stance: [強気 / 条件付き / 見送り / 要追加調査]
confidence: [高 / 中 / 低]
data_quality: [良 / ふつう / 悪]
freshness: [良 / ふつう / 悪]
red_flags: [R1, R2, ...]
verified_facts: [F12[S3], F13[S4], ...]
key_claims: [C12, C13, ...]
hypotheses: [H1, H2, ...]
open_questions: [Q1, Q2, ...]
next_actions: [A1, A2, ...]
sources: [S3, S4, ...]
