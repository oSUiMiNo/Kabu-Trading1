# 意見ログ: AMD set3

## メタデータ
- 銘柄: AMD
- セット: set3
- 意見番号: 1
- 入力ログ: AMD_set3.md
- 評価対象ラウンド: Round 1-3
- 評価日時: 2026-02-07 14:30

---

## 判定
- 支持（表示）: **BUY**
- 支持（機械）: BUY
- 勝者エージェント: analyst
- 勝因: conclusion
- 同点倒し: false

---

## スコア（0-100）
- 買い支持: **72**
- 買わない支持（様子見）: **68**
- 差分（買い-買わない）: **+4**

---

## 理由（支持できる根拠）

1. **2/6反発 +7.7% は市場パニック自己修正のシグナル** (log: Round3 / F19)
   → パニック売りピークを過ぎた段階での保有判断は妥当

2. **KeyBanc $270 PT はDevil想定を上回る市場の分化を示唆** (log: Round3 / F20, F21, F22)
   → CPU 50%+ 成長 + ASP 10～15% 上昇は業界標準的な見通しで信頼性あり

3. **OpenAI 6GW 供給契約は確定的な長期需要** (log: Round3 / F23)
   → 政治的不確実性より高い確度で、CEO「Inflection Year」の根拠となる

4. **China Cliff（$390M → $100M）は一時的調整、通期影響は限定的** (log: Round2 / F14, F15, Round3 / C6)
   → 年間ベース $400M～$600M（$1.5B ではなく）。米国・EU・日本向けCPU/GPU成長で補完可能

5. **Q1 ガイダンス -5% QoQ は「弱さの証」ではなく「誠実な現実化」** (log: Round3 / C7)
   → China一時出荷を除外した正常化ベース。Q2以降の次世代チップランプアップへの基盤整備期と解釈

6. **Devil の下値テスト（$170～$180）確度は著しく低い（20%未満）** (log: Round3 / C1, C2)
   → さらなる China 悪化ニュース + AI需要減速 + Q1決算追加下方修正の3つ同時が必須

---

## 反転条件

### 反転条件（ログで検証できる条件）
1. **OpenAI Instinct GPU 調達計画の公式延期・縮小** → BUY判定を NOT_BUY に転換
   根拠: 長期需要の柱が失われる (log: Round3 / F23)

2. **Q1 決算説明会（4月予想）での追加下方修正 + 通期ガイダンス大幅切り下げ** → BUY判定を NOT_BUY に転換
   根拠: 市場心理が「China 一時的」から「構造的不振」に転換する局面 (log: Round3 / C7)

3. **主要アナリスト（KeyBanc等）が $270 PT を一斉に $200 以下に下方修正** → BUY判定を NOT_BUY に転換
   根拠: Devil のシナリオが市場コンセンサス化する状況 (log: Round3 / 354)

4. **株価が $170 以下に調整 & 粗利率が 50% 以下へ低下** → 買い増し機会 → さらに強気に転換
   根拠: Devil 想定の下値テストが実現 = リスク・リワード が逆転 (log: Round3 / Devil反論)

5. **AI インフラ投資（Meta/Google/Microsoft）の cap ex 成長率が 30% 以下と確定** → BUY判定を NOT_BUY に転換
   根拠: AMD の $14B～$15B AI売上シナリオの根拠が揺らぐ (log: Round3 / 354)

### エントリー目安（提案）
1. **現在$197で保有** → 新規買いは $180～$190 の指値設定（Devil リスク管理）
2. **$220～$230 で 10～15% 利確** → アップサイドはまだ $270～$300 想定で十分
3. **$210 割れで追加買い（段階リバランス）** → 中期ホールド想定で選好

---

## 次に確認（最大3）
1. **Q1 2026 決算説明会（2026年4月予想）**
   - OpenAI GPU ランプアップの進捗確認が最優先
   - CPU ASP 実績が +10～15% 達成否かで、ベースシナリオの信頼度が確定

2. **中国 AI チップ規制動向**
   - 政治的転換で China 売上が Q2 以降 $100M から $200M+ へ拡大の可能性
   - または逆に $50M 以下への劇変リスク（Devil想定）も監視

3. **大型クラウドプロバイダー（Microsoft/Meta/Google）の 2026年 cap ex 公表**
   - AI インフラ投資加速の確度が高まれば、AMD の $14B～$15B AI売上シナリオも堅くなる
   - 成長率 30% 以下で Devil シナリオへの傾斜リスク

---

## 監査メモ（最大3）

1. **鮮度確認** - S1～S4（Round1）、S5～S7（Round2）、S8～S13（Round3）とも `retrieved_at: 2026-02-07` で統一。最新情報。ただし「2/6反発」は後続ニュース扱いで、本ログ記載時点（2/7 朝）から最大24時間前のデータ。市場心理は短時間で変動するため、現在価格確認推奨。

2. **根拠の支え確認** - Analyst の主張（C6～C11）はすべて Round3 の新規ファクト（F19～F26）で支えられており、Devil の主張（CC1～CC5）よりも新鮮性が高い。ただし KeyBanc $270 PT（F20～F22）は「アナリスト予想」であり、実績値ではなく。スコアリングでは「Devil との信頼度差」として評価済み。

3. **Devil の根拠チェック** - Devil が依拠する R1（「Q1 -5% QoQ は弱含みシグナル」）は、Round3 で Analyst が「China cliff を織り込んだ誠実なガイダンス」と再解釈された。この解釈差が「勝敗の分水嶺」だが、ログ内では Analyst が「China 一時出荷$390M の自然な落ち込み」を根拠に優位。ただし「一時出荷の自然さ」を市場が認める否かは、Q1決算説明会で初めて判明する領域。

---

## EXPORT（yaml）

```yaml
ticker: AMD
set: set3
opinion_no: 1

supported_side: BUY

buy_support_score: 72
notbuy_support_score: 68
score_diff: 4

winner_agent: analyst
win_basis: conclusion

threshold_margin: 5
decided_within_threshold: true
threshold_tipping: false

key_reasons:
  - reason_1: "Market self-correction signal (2/6 rebound +7.7%)"
    log_ref: "Round3 / F19"
  - reason_2: "KeyBanc upgrade to $270 PT (analyst consensus split favorable to upside)"
    log_ref: "Round3 / F20, F21, F22"
  - reason_3: "OpenAI 6GW supply contract (confirmed multi-year demand)"
    log_ref: "Round3 / F23"
  - reason_4: "China cliff impact limited to $400-600M annually, not structural"
    log_ref: "Round2 / F14, F15, Round3 / C6"
  - reason_5: "Q1 -5% QoQ is honest guidance not weakness signal"
    log_ref: "Round3 / C7"
  - reason_6: "Devil downside scenario (to $170-180) requires 3 simultaneous events, <20% probability"
    log_ref: "Round3 / C1, C2"

flip_triggers_high_confidence:
  - trigger: "OpenAI GPU supply plan officially delayed/reduced"
    probability: "10%"
    log_ref: "Round3 / 354"
  - trigger: "Q1 earnings call reveals further downside guidance + full-year cut"
    probability: "20%"
    log_ref: "Round3 / C7"
  - trigger: "KeyBanc & peers downgrade to <$200 PT in unison"
    probability: "20%"
    log_ref: "Round3 / 354"

next_monitoring:
  - q1_earnings_call_apr2026: "OpenAI ramp progress + CPU ASP achievement critical"
  - china_ai_regulation: "Risk range $100M-300M+ Q2 onward"
  - cloud_capex_guidance: "AI infrastructure growth <30% would invalidate AI $14-15B scenario"

audit_notes:
  - note_1: "Data freshness: all sources retrieved 2026-02-07. 2/6 rebound is ~24h old max. Current price re-check recommended."
  - note_2: "Analyst claims (C6-C11) backed by Round3 new facts (F19-F26), newer than Devil claims. KeyBanc PT is analyst forecast not actual result."
  - note_3: "Devil's key assumption (Q1 -5% = weakness) reinterpreted by Analyst as (honest China cliff normalization). This is pivotal but unresolved until Q1 earnings call."

evaluation_date: "2026-02-07 14:30"
```

---

**END OF OPINION**
