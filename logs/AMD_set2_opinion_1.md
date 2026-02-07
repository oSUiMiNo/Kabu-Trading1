# 意見ログ: AMD set2

## メタデータ
- 銘柄: AMD
- セット: set2
- 意見番号: 1
- 入力ログ: AMD_set2.md
- 評価対象ラウンド: Round 1-3
- 評価日時: 2026-02-07 12:00

---

## 判定
- 支持（表示）: **NOT_BUY (WAIT)**
- 支持（機械）: NOT_BUY_WAIT
- 勝者エージェント: analyst
- 勝因: conclusion
- 同点倒し: false

---

## スコア（0-100）
- 買い支持: 45
- 買わない支持（様子見）: 55
- 差分（買い-買わない）: -10

---

## 理由（支持できる根拠）
- 決算後17%下落は調整中であり、底打ち確認がまだできていない **(log: Round2 / F12)**
- Q1ガイダンス$9.8BはQoQ -4.6%で成長鈍化懸念が残る **(log: Round2 / F11)**
- OpenAI契約の売上貢献は2026H2以降で、Q1-Q2は業績寄与ゼロ **(log: Round2 / F13)**
- Analystの「ホールド」は論理的だが、保有中の売却検討案件であり、反発待ちのリスク許容が必要
- 季節要因（F15）はAnalystの反論として妥当だが、業界常識とされ具体ソースなし

---

## 反転条件
### 反転条件（ログで検証できる条件）
- Q1決算でガイダンス上振れ or 上方修正 **(log: Round3 / action_triggers)**
- 直近安値から出来高増加を伴う明確な反発 **(log: Round2 / flip_triggers)**
- OpenAI向け出荷開始の公式発表（売上規模明示）

### エントリー目安（提案）
- 現在保有中のため「新規買い」ではなく「ホールド継続 or 利確」の判断
- 直近安値を終値で明確に割り込んだ場合は損切り検討

---

## 次に確認（最大3）
- Q1決算発表（2026年4月頃）: ガイダンスが上振れるか確認
- 直近安値の具体的株価水準（ログに$XXXと未記載）
- OpenAI向け出荷開始の公式タイムライン

---

## 監査メモ（最大3）
- F14「過去4四半期連続ガイダンス上振れ」はS3,S4推定とされ、確定ソース弱い
- F15「季節要因」は「業界常識」とされ、具体ソースなし → 断定材料としては弱い
- 直近安値の具体的株価が$XXXプレースホルダーのまま（Round3 EXPORT内）

---

## EXPORT（yaml）
```yaml
ticker: AMD
set: set2
opinion_no: 1

supported_side: NOT_BUY_WAIT
buy_score: 45
not_buy_score: 55
winner_agent: analyst
win_basis: conclusion
same_score_fallback: false

key_reasons:
  - 底打ち未確認で追加下落リスク残存
  - Q1-Q2はOpenAI契約の業績寄与なし期間
  - ガイダンスQoQ減収への市場懸念は継続中

flip_triggers:
  - Q1ガイダンス上振れ or 上方修正
  - 出来高増加を伴う反発
  - OpenAI出荷開始の公式発表

next_check:
  - Q1決算発表
  - 直近安値の具体株価確認
  - OpenAI出荷タイムライン

audit_notes:
  - F14/F15のソースが弱い（推定/業界常識）
  - 直近安値が$XXXプレースホルダー
```
