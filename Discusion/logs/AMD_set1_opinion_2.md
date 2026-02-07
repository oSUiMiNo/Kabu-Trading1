# 意見ログ: AMD set1

## メタデータ
- 銘柄: AMD (NASDAQ: AMD)
- セット: set1
- 意見番号: 2
- 入力ログ: AMD_set1.md
- 評価対象ラウンド: Round 1-3
- 評価日時: 2026-02-07 12:00

---

## 判定
- 支持（表示）: **NOT_BUY (WAIT)**
- 支持（機械）: NOT_BUY_WAIT
- 勝者エージェント: analyst
- 勝因: debate_operation (Devil の単一視点 vs Analyst の段階的リスク修正が勝った)
- 同点倒し: true (差分 -7 で WAIT へ倒す)

---

## スコア（0-100）
- 買い支持: 55
- 買わない支持（様子見）: 62
- 差分（買い-買わない）: -7

---

## 理由（支持できる根拠）

1. **グロスマージン圧力が構造的**
   Q4実績 57% → Q1ガイダンス 46% の-11pp低下（F11, S10 log: Round3/F11）。AMD の「価格でシェア獲得」戦略は短期では EPS 成長を圧迫する可能性が高い。

2. **Intel Granite Rapids の実力がパリティ達成**
   Xeon 6900P が 128コアで EPYC Bergamo と同等化（F13, S12 log: Round3/F13）。Devil の「限定的脅威」評価が修正され、2026 Q2-Q3 の顧客購買シフトリスクが増加。

3. **EPS $20 目標の前提条件が崩れやすい**
   CAGR 35% は当初想定だが、マージン 46% 開始時は CAGR 40%+ が必要になり、実現難度が上昇（F14, C11 log: Round3/C11）。

4. **Q1決算（5月5日）が重要判定ポイント**
   グロスマージン 46% 実績 + YoY成長実績で売却可能性が高まる。現在 $192 の株価は調整途上の可能性（log: Round3/C1, Analyst修正）。

5. **Analyst の段階的な主張修正が説得力**
   Devil の「季節パターン」指摘を認めつつも、マージン圧力と Intel 脅威を重視する判断は、根拠（F11-F14）に基づいており、机上の楽観より信頼度が高い。

6. **短期（～5月）は様子見が合理的**
   Round 3 での Analyst の「ホールド・Q1決算で最終判定」アクションは、リスク回避と情報待ち戦略として実務的（log: Round3/Analyst修正アクション）。

---

## 反転条件

### 反転条件（ログで検証できる条件）
1. **Q1 2026 決算で YoY成長が 32% 以上を達成** かつ **グロスマージン実績が 48% 以上** → 予想比上振れで買い検討へ
2. **Intel Granite Rapids 導入が想定より遅延**（2026 Q2 船り出遅延確認） → 競争優位性が一時的に保全、買いに転じる可能性
3. **マクロ金利が 4.5% 以下へ急低下** → テックセクター全体の再評価で、グロース割安化
4. **NVIDIA の AI ビジネスが明らかに失速** → AMD AI GPU 市場独占の可能性が高まり、買い直し材料へ

### エントリー目安（提案）
- **$165 以下** への下落で買い回線の提案（PE 50 相当、安全マージン確保）
- **$190～200 レンジ保持** で様子見継続（現在値 $192.50 の周辺）
- **Q1決算発表直後 3営業日以内** に主張が反転するデータが出た場合、即座に評価修正

---

## 次に確認（最大3）

1. **Q1 2026 決算（2026年5月5日予定）の実績**
   グロスマージン 46% の実現確認、YoY成長率 32% 上下判定、次期ガイダンスの方向性が必須。

2. **Intel Granite Rapids / Diamond Rapids の顧客採用動向**
   2026 Q2-Q3 の OEM/クラウド顧客の購買シフト実績。Analyst が指摘した「脅威レベル高い」の検証。

3. **AMD の AI GPU（MI450）ラップの実売上見通し**
   H2 2026 開始予定の「tens of billions」規模到達の蓋然性。Devil の強気要因の再検証。

---

## 監査メモ（最大3）

1. **根拠不足：株価 $192.50 の「適正 PE」が 60 か 65 か不確定**
   Round 3 で Analyst が「PE 60～65 水準への調整の可能性」と述べたが、グロスマージン 46% での予想 EPS が確定していないため、下値目安が若干曖昧。ただし判断自体は合理的な範囲。

2. **鮮度トリガー成立：Q1ガイダンス情報（S10, 2026-02-03）が中核事実**
   retrieved_at: 2026-02-07（本日）で最新。ただしガイダンスは予想値のため、実績との乖離可能性は常に存在。次決算での検証が必須。

3. **重大リスク：「AI ブーム終焉」の可能性が Devil の確度 72% 内で 20% と軽視されている可能性**
   現在のマージン圧力・Intel 対抗投資が、仮に AI 需要が予想より低迷した場合、デュアルリスク（成長鈍化＋高コスト）に転じる懸念。Round 1-3 では詳細分析されていない。

---

## EXPORT（yaml）

```yaml
ticker: AMD
set: set1
opinion_no: 2

supported_side: NOT_BUY_WAIT
support_score_buy: 55
support_score_wait: 62
score_diff: -7
tie_break_rule_applied: true

winner_agent: analyst
win_basis: debate_operation

confidence_level: 68

key_decision_factor:
  - gross_margin_pressure_11pp_cut
  - intel_competitive_threat_escalated
  - q1_earnings_judgment_point_critical
  - analyst_staged_risk_update_credible

decision_horizon: short_term_watch_5_months
next_check_date: 2026-05-05
current_stock_price: 192.50
current_pe_ratio: 79
target_pe_range: 60-65

audited_by: opinion_agent_set1_k2
audited_at: 2026-02-07
audit_items_flagged: 3
```

