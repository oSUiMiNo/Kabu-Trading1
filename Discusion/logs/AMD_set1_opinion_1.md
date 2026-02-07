# 意見ログ: AMD set1

## メタデータ
- 銘柄: AMD (NASDAQ: AMD)
- セット: set1
- 意見番号: 1
- 入力ログ: AMD_set1.md
- 評価対象ラウンド: Round 1-3
- 評価日時: 2026-02-07 13:30

---

## 判定
- 支持（表示）: **NOT_BUY (WAIT)**
- 支持（機械）: NOT_BUY_WAIT
- 勝者エージェント: analyst
- 勝因: debate_operation（Round 3 で新事実 F11-F14 による修正）
- 同点倒し: true

---

## スコア（0-100）
- 買い支持: 55
- 買わない支持（様子見）: 60
- 差分（買い-買わない）: -5

---

## 理由（支持できる根拠）

1. **グロスマージン -11pp 低下の確定** (log: Round 3 / F11)
   - Q4 2025実績 57% → Q1 2026ガイダンス 46% の大幅低下
   - EPS $20 目標達成の必須条件（利益成長）が揺らいでいる

2. **Intel Granite Rapids パリティ達成の脅威** (log: Round 3 / F13)
   - Xeon 6900P が128コアで EPYC Bergamo と完全パリティ
   - DC セグメント +39% YoY の成長が Q2-Q3 で失速リスク

3. **決定トリガーが明確に設定済み** (log: Round 3)
   - Q1 2026決算（5月5日）で GM 実績確認後に最終判定
   - 約3ヶ月間の待機で根拠が固まる

4. **Devil の「季節パターン」主張は正当・一部譲歩済み** (log: Round 2 / C6)
   - Q1 YoY +32% の鈍化は季節要因の可能性を Analyst も認めた (Round 3)
   - ただし、マージン圧力はそれ以上に懸念材料

5. **現在株価 $192 は PE 60 への調整途上** (log: Round 3)
   - Devil の「買い機会」説も、新マージン データで PE 再評価が必要
   - 短期反発余地（$200-210）はあるが、上値は限定的

6. **「一時的か構造的か」未確定が最大の不確実性** (log: Round 3 / C9)
   - AMD 公式の「3～5年で回復」説 (F12) があるも、確定ではない
   - Q1 決算で初めて実績値で確認できる

---

## 反転条件

### 反転条件（ログで検証できる条件）

1. **Q1 2026決算（5月5日）でグロスマージン 46% 実績 + YoY 成長 32% 達成**
   - この場合、Devil の「季節・マージン一時的」説が確度向上 → **BUY へ傾斜**

2. **Intel Diamond Rapids（H2 2026）の市場受け入れが弱い**
   - 顧客の DC 投資が AMD 継続選択 → DC 成長継続の確度向上 → **BUY**

3. **MI450 ラップが Q2-Q3 で目標出荷を達成**
   - AI 需要継続の証拠となり、2027 年「tens of billions」目標の信用向上 → **BUY**

4. **グロスマージン Q2 で 50% 以上に回復**
   - マージン圧力が「一時的」の確認 → **BUY 継続判定へ昇格**

5. **マクロ環境の金利低下トレンド**
   - テック成長株への投機的需要が復活 → 株価回復 → **BUY の参入機会**

### エントリー目安（提案）

1. **$180 割れ出現時の買い検討** - Q1 決算までの安値保証なし
2. **$250 回復時の利益確定売却検討** - 年初水準への復帰確認まで
3. **決算後の MI450 需要確認** - 目標出荷達成が買い再開の最重要指標

---

## 次に確認（最大3）

1. **Q1 2026決算（2026年5月5日）でグロスマージン 46% が実績値で確認されるか**
   - 「一時的」か「構造的」かの判定が最優先
   - 同時に DC セグメント成長率の実績確認も必須

2. **Intel Diamond Rapids（H2 2026）のサンプル出荷・顧客評価**
   - Granite Rapids と異なり、16ch メモリ対応で EPYC Venice に逆転脅威
   - Q3-Q4 2026 の顧客シフト実績で AMDの優位性が本当に持続するか確認

3. **MI450 GPU ラップのスケール到達タイミング**
   - H2 2026 で「tens of billions 規模」への道筋確認
   - 2027年見通しの信用度が左右される

---

## 監査メモ（最大3）

1. **鮮度・根拠不足**
   - S10, S12, S13 の retrieved_at がすべて 2026-02-07（本日）で、一部は推測/リークベース
   - Intel Diamond Rapids H2 展開は「予定」であり確定ではない
   - 監査結論：F13 の「脅威レベル高い」は蓋然性は高いが、断定は避ける（理由の主軸にしない）

2. **Analyst の「部分譲歩」と最終判定の矛盾リスク**
   - Round 3 で「ホールド・Q1決算で最終判定」と述べながら、実は 3つのリスク（マージン・Intel・成長）の不確実性が残存
   - この場合、WAIT が正当だが、Devil 寄りの投資家には「弱気過ぎる」と映る可能性
   - 対応：本意見では WAIT を採用し、Q1 決算で再評価する設計とした

3. **Devil の「PEG 2.1 は割安」論の検証漏れ**
   - Round 2 で C5 が PEG 2.1 論を展開したが、マージン 46% 前提では PEG 2.5+ に悪化
   - ログでは Round 3 で修正されているが、元の Devil 主張の説得力が減じられたことを明記
   - 監査結論：Devil の「買い機会」主張は Round 2 時点では根拠が弱かった（ただし季節説は正当）

---

## EXPORT（yaml）

```yaml
ticker: AMD
set: set1
opinion_no: 1

supported_side: NOT_BUY_WAIT
buy_score: 55
not_buy_score: 60
score_diff: -5
threshold_applied: true
threshold_value: 5

winner_agent: analyst
win_basis: debate_operation
unanimous: false
flip_triggered_in_round: 3

decision_date: 2026-02-07
next_review_date: 2026-05-05

key_facts:
  - F11_gross_margin_q1_46pct_vs_q4_57pct
  - F13_intel_granite_rapids_parity_achieved
  - F9_mi450_ramp_h2_2026

risk_level: medium_to_high
confidence: 68

audit_notes:
  - "Intel Diamond Rapids is planned, not confirmed"
  - "Analyst revised PEG calculation, Devil baseline weakened"
  - "Decision gate at Q1 earnings is appropriate for margin verification"

ready_for_action: false
action_type: WAIT
trigger_mechanism: "Q1_2026_earnings + gross_margin_validation"
```
