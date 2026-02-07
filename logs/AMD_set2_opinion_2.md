# 意見ログ: AMD set2

## メタデータ
- 銘柄: AMD (Advanced Micro Devices)
- セット: set2
- 意見番号: 2
- 入力ログ: AMD_set2.md
- 評価対象ラウンド: Round 1-3
- 評価日時: 2026-02-07 15:30

---

## 判定
- 支持（表示）: **BUY**
- 支持（機械）: BUY
- 勝者エージェント: analyst
- 勝因: conclusion
- 同点倒し: false

---

## スコア（0-100）
- 買い支持: 82
- 買わない支持（様子見）: 18
- 差分（買い-買わない）: 64

---

## 理由（支持できる根拠）

1. **MI400採用の確度向上** — Round 3で Meta/AWS/Microsoft による CES 2026 公表済み。「受注ゼロ」から「採用確定」へ 確度が飛躍的に上昇 (log: Round3 / F1-Analyst)

2. **17%急落の実体は一時要因** — Q4 China MI308 $390M → $1 00M への落ち込みが主因。本体 Data Center 事業は「sequential up」継続 (log: Round3 / F2-Analyst)

3. **PER 30xは既に調整済み** — 17%急落後の現在値$208.63でPER 30x＝むしろ割安段階。NVIDIA（PER 35x）と比較しても相対的割安 (log: Round3 / F4-Analyst)

4. **リスク・リターンが保有有利** — Bull確度 85%, 期待値 +28% (修正後)。下値防衛が機能（採用確定後の失敗確度12%）(log: Round2-3 / C2-Analyst修正)

5. **Hyperscaler ASIC開発が追い風** — Microsoft除く Google/Meta/AWS の自社チップ開発加速＝NVIDIA支配弱化。AMD「唯一実行可能代替案」の地位強化 (log: Round3 / F5-Analyst)

6. **Devil's advocate の flip_triggers 一部発動** — 「MI400採用複数hyperscaler公式確認」が既に実現。逆転条件がクリアされた (log: Round3 / C4-Analyst)

---

## 反転条件
### 反転条件（ログで検証できる条件）

1. **MI400 shipment ramp 計画下回り** — Q1決算で「初期受注」が期待値-30%以上の場合（修正後の Bull確度85%は shipment実績ベース）

2. **Data Center 利益率が 50%割れ** — 55%→50% への低下＝構造的な価格競争激化の兆候。(現在57%から55%の低下は China要因と判定済み)

3. **NVIDIA が AI GPU 市場シェア拡大ニュース** — 目標株価$256.82 の EPS成長前提が崩れる場合

4. **セクター全体の需要後退ニュース** — Hyperscaler capex計画の下方修正や、加速コンピューティング CAGR 42% 見通しの大幅下修

### エントリー目安（提案）

1. 目標株価 $256.82（現在値比+21.84%）到達後の利益確定
2. Q1決算（5月）で MI400 shipment rate +20%超達成時は上方修正検討
3. PER 30x 維持かつ粗利益 55%超確保が確認されれば、さらに上値$289+ も視野

---

## 次に確認（最大3）

1. **Q1決算（5月予定）MI400 shipment rate と契約額** — Round 3 では「初期QC完了＆月間rate開示」を想定。実績値で Bull確度 85% の検証必須

2. **Hyperscaler capex公表（Google/Meta/AWS）** — AMD MI400 採用予算が「初期」vs「本格」かの判別。今後12ヶ月の出荷計画の可視化

3. **粗利益率の回復見通し** — 55%維持 vs 57%回復。中国要因の終焉で主力製品へのミックスシフトが確実か

---

## 監査メモ（最大3）

1. **S9-S13 の鮮度確認** — Round 3 で追加されたソースは全て "2026-02-07" で同一日。CES 2026 公表情報（S9）の確度は高いが、Amazon/Google/Meta の個別公開リリース確認を強く推奨

2. **Devil の「下値25-40%」リスク評価の根拠不足** — セクター後退パターン（C4-Devil）は一般的な仮定。Analyst の「AI Rationalization＝成熟段階」との相反に対し、Devil が再論拠を示していない。Round 3 では Analyst の再評価が優先

3. **PER 比較の簡略性** — NVIDIA PER 35x vs AMD PER 30x の比較（F4-Analyst）は「成長率」を排除。NVIDIA 成長率が AMD より低い場合の相対評価には追加検証が必要だが、ログに成長率比較なし

---

## EXPORT（yaml）

```yaml
ticker: AMD
set: set2
opinion_no: 2

supported_side: BUY
buy_support_score: 82
notbuy_support_score: 18
difference: 64

winner_agent: analyst
win_basis: conclusion
threshold_margin: 5
triggered_flip: true
flip_reason: MI400_adoption_confirmed_by_hyperscalers

key_decision_factors:
  - MI400 採用実績が Round 3 で公表済み（Meta/AWS/Microsoft）
  - 中国一時特需終焉が 17% 急落の主因であることが確認
  - リスク・リターン非対称性が保有有利へ反転（Bull確度 85%, 期待値 +28%）
  - Hyperscaler ASIC 開発加速により AMD 相対競争力が向上

confidence_level: 82
confidence_note: "MI400採用確定は high confidence。ただし実際の shipment ramp と利益率回復は Q1決算待ち。現在 HOLD+ 判定は妥当だが、エントリー/加算の判定は決算結果次第"

recommendation: HOLD_WITH_UPSIDE
holding_period: until_Q1_2026_earnings_May
take_profit_target: 256.82
stop_loss_trigger: datacenter_margin_below_50_or_mi400_shipment_miss_30pct
```
