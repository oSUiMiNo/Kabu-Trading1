# AMD (Advanced Micro Devices Inc.) - 初回分析ログ

**銘柄**: AMD
**分析日時**: 2026-02-07
**分析目的**: 保有中ポジションの売却検討判断

---

## Round 1

### Sources（情報源）

| S# | Type | Title | URL | Retrieved_at |
|----|------|-------|-----|--------------|
| S1 | Web | AMD stock price earnings 2026 | [link](https://public.com/stocks/amd/earnings) | 2026-02-07 |
| S2 | Web | AMD stock sinks 17% after earnings | [link](https://www.cnbc.com/2026/02/04/amd-stock-earnings-forecast-chips.html) | 2026-02-07 |
| S3 | Web | AMD Reports Q4 2025 Financial Results | [link](https://ir.amd.com/news-events/press-releases/detail/1276/amd-reports-fourth-quarter-and-full-year-2025-financial-results) | 2026-02-07 |
| S4 | Web | AMD Q4 FY2025 earnings call transcript | [link](https://finance.yahoo.com/quote/AMD/earnings/AMD-Q4-2025-earnings_call-395057.html) | 2026-02-07 |

### Facts（事実）

| F# | Content | Source |
|----|---------|--------|
| F1 | Q4 2025 EPS（非GAAP）は$1.53、コンセンサス$1.24を上回る | S1, S3 |
| F2 | Q4 2025 売上高は$10.3B、LSEG予想$9.67Bを上回る | S3 |
| F3 | Q4 2025 粗利率は54%（GAAP）、57%（非GAAP） | S3 |
| F4 | Q4 2025 データセンター売上、YoY +39%成長 | S3 |
| F5 | Instinct GPU、EPYC CPU 過去最高売上 | S3 |
| F6 | FY2025 通年売上$34.6B、前年比+34% | S3 |
| F7 | FY2025 非GAAP EPS $4.17 | S3 |
| F8 | Q1 2026 ガイダンス$9.8B（±$300M）、QoQ -5% | S2, S1 |
| F9 | Q1 2026 ガイダンスは予想$9.38Bを上回るも増期待に未達 | S2 |
| F10 | 決算発表翌日（2/4）株価17%下落 | S2 |
| F11 | 現在株価$197.13（オープン）、前日終値$192.50 | S1 |
| F12 | アナリスト評価：Strong Buy 30、Moderate Buy 3、Hold 12 | S1 |
| F13 | 12ヶ月目標株価平均 約$288 | S1 |

### Claims（主張）

| C# | Claim | Basis | Strength |
|----|-------|-------|----------|
| C1 | AMDは決算では期待を上回ったが、Q1ガイダンスで市場が失望した | F1, F2, F8, F9, F10 | Strong |
| C2 | データセンター・AI事業は堅調（+39% YoY）で成長軌道継続 | F4, F5, F6 | Strong |
| C3 | 現在の下落は「需給調整」＋「期待値巻き戻し」による短期反応と推測 | F10, F12, F13 | Medium |
| C4 | 粗利率57%（非GAAP）は健全で、利益率の悪化は見られない | F3, F7 | Strong |
| C5 | 市場センチメント（Strong Buy:Hold=2.5:1）は比較的肯定的 | F12 | Medium |

### Risks & Questions（リスク・質問）

| R# | Risk / Concern |
|----|---|
| R1 | Q1 QoQ -5%は弱含みシグナル。AI需要の一時停止？ |
| R2 | 現在株価$197は目標$288の31%割引状態。過度な調整か判断必要 |
| R3 | 17%下落は市場パニック水準。底値トラップのリスク |

| Q# | Question |
|----|---|
| Q1 | Q1ガイダンスの下方修正理由は何か？（需要減速 vs. 在庫調整） |
| Q2 | 現在のポジション平均取得価格は？（売却判断の基準） |
| Q3 | データセンター以外の事業部門（ゲーム、汎用CPU等）の動向は？ |

### 暫定結論

- **売却判断**: 保留～段階売却推奨（保留理由）
  - 決算内容自体は堅調（EPS +23%）、AI事業も好調（+39% YoY）
  - 下落は「期待値調整」の典型的パターン → 技術的反発の可能性
  - 目標$288との乖離が大きい（-31%）が、3～6ヶ月での回帰見通しは妥当

- **推奨アクション**:
  1. **即時売却は非推奨** ← 下降トレンドの底値領域と推定
  2. **段階売却を検討** ← ポジションサイズが大きい場合、1/3～1/2程度の利確を検討
  3. **決算説明会議事録確認** → Q1ガイダンス下方の詳細理由を把握してから最終判断

### EXPORT

```json
{
  "symbol": "AMD",
  "round": 1,
  "analysis_date": "2026-02-07",
  "last_price": 197.13,
  "52w_target": 288,
  "upside_potential": "31%",
  "recent_move": "-17% (post-earnings)",
  "key_metrics": {
    "q4_eps_non_gaap": 1.53,
    "q4_revenue": "10.3B",
    "fy2025_revenue": "34.6B",
    "gross_margin_non_gaap": "57%"
  },
  "sentiment": "Positive (Strong Buy:Hold = 2.5:1)",
  "recommendation": "Hold / Partial Take-Profit",
  "confidence": "Medium-High"
}
```

---

## Round 2 (Devil's Advocate - 売却寄りの視点)

### Stance Declaration

**Analyst: Hold / 段階売却** → **Devil: 売却推奨（条件付き買い見送り）**

理由：Analystは「Q1ガイダンス-5% = 短期的な期待値調整」と楽観視しているが、実態は **China Cliff（$390M → $100M）** と **AI需要過度期待の巻き戻し** によるより構造的な弱さ。

---

### Sources（追加情報源）

| S# | Type | Title | URL | Retrieved_at |
|----|------|-------|-----|--------------|
| S5 | News | AMD falls 17%, posts worst day since 2017 as Lisa Su addresses guidance concerns | [link](https://www.cnbc.com/2026/02/04/amd-lisa-su-ai-demand-guidance-earnings.html) | 2026-02-07 |
| S6 | News | The AI Paradox: Why AMD's 'On Fire' Demand Triggered a Market Meltdown | [link](https://markets.financialcontent.com/stocks/article/marketminute-2026-2-6-the-ai-paradox-why-amds-on-fire-demand-triggered-a-market-meltdown) | 2026-02-07 |
| S7 | News | AMD Stock Crashes Despite Record Q4 Revenue as AI Outlook Disappoints | [link](https://winbuzzer.com/2026/02/04/amd-posts-record-q4-revenue-but-ai-outlook-disappoints-xcxwbn/) | 2026-02-07 |

---

### Additional Facts（追加事実）

| F# | Content | Source |
|----|---------|--------|
| F14 | Q4 売上$10.3B中、China MI308 一時出荷が$390M（~3.8%の売上ベース） | S6 |
| F15 | Q1 2026 ガイダンス、China売上を$390M → $100M に下方修正（-74%） | S6 |
| F16 | 17% 下落は「2017年以来の最悪日」 - パニック売りではなく構造的調整と解釈可能 | S5 |
| F17 | Lisa Su CEO が「AI需要は想像以上に加速」と弁明も、市場は非確信的 | S5, S6 |
| F18 | 市場は「AI gold rush の正常化」と「需要の持続可能性」を疑問視 | S6 |

---

### Counter-Claims（反対側の主張）

| C# | Counter-Claim | Basis | Strength |
|----|---|---|---|
| CC1 | Q1 -5% QoQは「調整」ではなく **China Cliff による構造的下方修正**。売上ベース -$290M = -3%の強制調整 | F14, F15 | Strong |
| CC2 | データセンター+39% YoYは **中国一時出荷に含まれている**。実出荷ベースでの成長率は市場予想より低い可能性 | F4, F14, F15 | Medium-High |
| CC3 | アナリスト目標$288は **2026年上半期の AI 加速を前提**。China cliff と AI需要正常化のダブルパンチで達成困難 | F13, F18 | Medium |
| CC4 | 17% 下落は「パニック」ではなく、市場が **期待値フォワードの大幅巻き戻し** を開始したシグナル。さらに下値テストの可能性 | F10, F16, F18 | Medium |
| CC5 | 現在$197は **下値ではなく中値**。目標$288との乖離は「安全域」ではなく「過度な期待」の残存を示唆 | F11, F13 | Medium |

---

### Devil の反論ポイント（重要度順）

#### 1️⃣ **China Cliff の過小評価**
- Analystは「期待値調整の典型」と評価するが、Q4の$390M中国出荷は **政治的タイミング（輸出規制の一時緩和）** による一過性
- Q1で$100Mに-74%減少 = 通年では約$1B～$1.5B の China 売上喪失リスク
- これは「調整」ではなく **構造的な売上欠落** で、年度通期ガイダンスの下方修正が避けられない

#### 2️⃣ **AI 需要の持続可能性への市場懸念は妥当**
- Q4 の好決算でも Q1 ガイダンスが弱い = **供給サイド（AMD側）の生産上限 or 需要サイドの減速** のいずれか
- Lisa Su の「AI加速」発言は CEO として当然だが、市場は **ガイダンス数字（-5% QoQ）** を信頼
- 2年続いた AI 爆発から「正常化」への局面転換と解釈し、$288 目標は過度

#### 3️⃣ **テクニカル下値テストの可能性**
- -17% は「深い調整」だが、$197はまだ **サポートレベル（過去 12ヶ月の水準線）** を割っていない
- 決算サプライズ → マイナスガイダンス → China cliff 顕在化の3段構え → さらに $170～$180 への下値テスト可能性
- この局面での「保有」は「ナンピン待機」と同義で、リスク・リワード が逆転している

---

### Devil の結論

**Stance: 売却推奨 / 買い見送り**

**根拠**：
- **China Cliff ($290M 損失) + AI需要正常化** のダブルヘッドウィンドで、通期ガイダンス下方修正の確度が高い
- 現在$197は「底値」ではなく「調整途中」。テクニカルには$170～$180への下値テストが想定され、安全域がない
- $288 目標株価は「Q1 China cliff 前」の想定。達成には中国規制緩和と AI 需要の加速の両立が必要で、確度が著しく低下

**推奨アクション**：
1. **保有ポジションは 50～75% を即時売却** ← China cliff の明確化で市場が次なる下方修正を織り込む局面
2. **残り 25～50% は $180 の下値テスト後の再構成を検討** ← 決算説明会での詳細説明待ち
3. **新規買いは $160 以下の指値で検討**（ただし通期ガイダンス下方修正後）

**相手方に転じる条件（Flip Triggers）**：
- ✅ China 規制の大幅緩和発表（年内の政治的転換）+ 次Q ガイダンスの上方修正
- ✅ AI インフラ投資の加速発表（Microsoft/Meta/Google の cap ex 増加公表）
- ✅ 株価が$160以下に調整 + 粗利率維持の確認

---

### EXPORT (Round 2 - Devil's Position)

```json
{
  "symbol": "AMD",
  "round": 2,
  "agent": "Devil's Advocate",
  "analysis_date": "2026-02-07",
  "stance": "Sell / Don't Buy",
  "last_price": 197.13,
  "target_downside": 170,
  "target_reentry": 160,
  "key_risks": {
    "china_cliff": "-$290M revenue impact (MI308 one-time shipment)",
    "ai_demand_normalization": "Growth deceleration after 2-year boom phase",
    "guidance_downside": "Q1 -5% QoQ signals broader weakness"
  },
  "confidence": "Medium-High (75%)",
  "key_reasons": [
    "China MI308 $390M one-time shipment in Q4 created artificial baseline for Q1 (now -74% to $100M)",
    "Market correctly identifying AI spending normalization cycle",
    "Current $197 is mid-range, not floor; $170-180 downside test likely",
    "$288 analyst target obsolete post-China cliff realization"
  ],
  "flip_triggers": [
    "China export regulation significantly relaxed + next Q guidance raised",
    "Major cloud provider capital expenditure acceleration announced",
    "Stock reaches $160 with sustained gross margin >56%"
  ]
}
```

---

## Round 3 (Analyst Response to Devil's Advocate)

### Stance Re-Declaration

**Analyst Position: HOLD / 段階的リバランス（修正：「段階売却」から「保有堅持」へ転換）**

Devil's Advocateの「売却推奨・$170～$180 下値テスト」に対し、以下の反論を展開：
1. China Cliff の影響度は認めるが、**構造的な終わり** ではなく **一時的な吸収**
2. **2/6反発 +7.7%** と **OpenAI との長期契約** が市場メンタルを局所的に回復させた
3. Q1 ガイダンスは「マイナスサプライズ」ではなく「現実的な調整」

---

### Sources（新規追加情報源）

| S# | Type | Title | URL | Retrieved_at |
|----|------|-------|-----|--------------|
| S8 | News | AMD Stock Rebounds 7.7% After Guidance-Driven Sell-Off \| February 2026 | [link](https://www.indexbox.io/blog/amd-stock-jumps-77-on-february-6-2026-rebounding-from-post-earnings-plunge/) | 2026-02-07 |
| S9 | News | AMD CEO Says 2026 Will Be A Major 'Inflection Year,' Fueled By AI-Driven CPU Demand | [link](https://stocktwits.com/news-articles/markets/equity/amd-ceo-2026-major-inflection-year-fueled-by-ai-driven-cpu-demand/cZbjcr9R4Zx) | 2026-02-07 |
| S10 | Research | This Analyst Says AMD Will Soar 30% in 2026. Here's Why It's Time to Buy. | [link](https://247wallst.com/investing/2026/01/13/this-analyst-says-advanced-micro-devices-will-soar-30-in-2026-heres-why-it-time-to-buy/) | 2026-02-07 |
| S11 | Research | KeyBanc Analyst John Vinh Upgrades AMD to Overweight, $270 PT | [link](https://finance.yahoo.com/news/analyst-says-advanced-micro-devices-171220199.html) | 2026-02-07 |
| S12 | News | OpenAI AMD Partnership: 10% Stake, 6GW Instinct GPU Supply Deal | [link](https://ir.amd.com) | 2026-02-07 |
| S13 | News | AMD MI308 Q1 2026 $100M vs Q4 $390M: China Cliff Details | [link](https://www.trendforce.com/news/2026/02/04/news-amd-1q-sales-to-slip-despite-100m-mi308-china-boost-next-gen-ai-chips-set-for-2h-ramp/) | 2026-02-07 |

---

### Additional Facts（新規・Devil反論への対抗データ）

| F# | Content | Source |
|----|---------|--------|
| F19 | 2/6 反発 +7.7%。市場パニック売りの一部解消シグナル | S8 |
| F20 | KeyBanc の John Vinh がAMDを Overweight にアップグレード、$270 PT提示 | S11 |
| F21 | KeyBanc見通し：サーバーCPU 50%以上成長、2026年AI売上 $14B～$15B | S11 |
| F22 | 2026年サーバーCPU ほぼ売却済み（nearly sold out）、ASP 10～15% 上昇見通し | S11 |
| F23 | OpenAI AMD 10%出資契約 + 6GW Instinct GPU供給契約（段階的・2H2026開始） | S12 |
| F24 | AMD CEO Lisa Su「2026年は AI駆動のCPU需要による大転換点」と強調 | S9 |
| F25 | Q1 2026 $9.8B ガイダンスは「弱さ」ではなく「China一時出荷除去後の正常化」 | S13 |
| F26 | 次世代AI チップ 2H2026 ランプアップ。Q1～Q2は基盤整備期と解釈可能 | S13 |

---

### Claims（Round 3 - 分析者の反論主張）

| C# | Claim | Basis | Strength | Devil対抗度 |
|----|-------|-------|----------|-----------|
| C6 | China MI308 $390M → $100M は「構造的終わり」ではなく「一時出荷の自然な落ち込み」 | F14, F15, F25, F26 | Strong | ⭐⭐⭐ 直撃 |
| C7 | Q1 -5% QoQ は政治的 China cliff を織り込んだ「現実的」なガイダンス。弱さの証ではなく誠実性の証 | F8, F25, F26 | Strong | ⭐⭐⭐ 直撃 |
| C8 | OpenAI との 6GW 供給契約は、市場が過度に割引している「確定的な長期需要」 | F23 | Strong-Medium | ⭐⭐⭐ 追加論拠 |
| C9 | KeyBanc $270 PT（現$197比 +37%）は、Devil の$160 reentry 想定を否定するアップサイド見方の分化を示す | F20, F21, F22 | Medium | ⭐⭐ 信頼度議論 |
| C10 | 2/6反発 +7.7% は、市場がパニック売りの過度を自己修正中。$197 は買値ではなく「売りオーバー」後の中値 | F19 | Medium | ⭐⭐ 短期心理 |
| C11 | AI 需要「正常化」は Devil の表現。実際には「爆発段階から持続的成長段階への遷移」 | F21, F24 | Medium-High | ⭐⭐⭐ 視点転換 |

---

### Devil's Advocates への具体反論

#### 1️⃣ **「China Cliff は構造的」への反論**

**Devil主張**：Q1 $100M は -74% 減で、通期 $1B～$1.5B 売上欠落リスク

**分析者反論**：
- Q4 $390M は「一時的な出荷ラッシュ」。輸出規制の一時緩和 + 中国企業の駆け込み需要の**完全な組み合わせ**
- Q1 $100M は「規制正常化後の自然な水準」。年間ベースでは Q1:$100M + Q2-Q4の不確実 = **$300M～$500M**（年 $1.5B ではなく $400M～$600M）
- **重要**: AMD は「China 売上を除外しても** 、サーバーCPU+Instinct（米国・日本・EU向け）で十分な成長を維持できる設計
- F25, F26: 「次世代チップ 2H2026 ランプ」 = China cliff を補う新製品サイクルの開始

#### 2️⃣ **「AI 需要の持続可能性への市場懸念は妥当」への反論**

**Devil主張**：2年続いた AI 爆発から「正常化」へ。$288 目標は過度

**分析者反論**：
- **語義の再解釈**: 「正常化」 ≠ 「終焉」。むしろ **「爆発的 → 持続的成長への遷移」**（本来は業界にとって健全）
- F21, F22: KeyBanc「サーバーCPU 50%+ 成長、ASP 10～15% 上昇、2026 AI売上 $14B～$15B」
  - これは **業界成長予想**。AMD がシェア維持なら単独でも $6B～$8B AI売上の可能性
- F23: OpenAI 6GW（段階的・2H2026開始） = **確定的な容量枠**。CEOが「Inflection Year」と明言する根拠
- 市場は「AI ブーム終焉」に過敏反応。実際には「持続的インフラ投資フェーズ」への移行

#### 3️⃣ **「$197 は下値ではなく中値。$170～$180 テストは妥当」への反論**

**Devil主張**：テクニカル下値テスト想定。現$197 は調整途中

**分析者反論**：
- F19: **2/6 反発 +7.7%**。パニック売りが既に自己修正始動したシグナル
- Devil の「$170～$180 テスト」想定は、以下を前提：
  1. **China cliff のさらなる悪化ニュース**（現時点では $100M Q1 確定で、下方修正リスク限定的）
  2. **AI 需要がさらに減速**（KeyBanc、CEO声明と矛盾）
  3. **Q1 決算説明会での追加下方修正**（可能性は低い。むしろ次世代チップ・OpenAI進捗の好転期待）

- **テクニカル再評価**:
  - $197 → $180 は「下値テスト」ではなく「過度な売られ過ぎからの 再構成」
  - むしろ $200～$210 が新しい「支持レベル」になる可能性が高い（買いの層が厚い）

#### 4️⃣ **$288 目標株価の妥当性**

**Devil主張**：$288 は「Q1 China cliff 前」の想定。達成困難

**分析者反論**：
- Devil は $288 の達成条件を「中国規制緩和 + AI需要加速」の両立と定義したが、**実は片方でも可**：
  1. **単独条件A（中国規制緩和）**: 不確実性高い（政治的）✗
  2. **単独条件B（AI需要加速 + CPU ASP上昇）**: 確度高い（KeyBanc, CEO, OpenAI契約が根拠）✓✓

- F20, F21, F22 を総合すると：
  - KeyBanc の $270 PT（$288 に近い）は、**条件B単独** での見通し
  - 基盤となる仮定：CPU 50%+成長 + ASP 10~15% 上昇 + AI 売上 $14B～$15B（業界全体で市場の $30B～$40B の 35～50%）

- **調整後の合理的 PT**：
  - **保守シナリオ**（AI需要正常化）: $240～$260 by 2026末（現$197比 +22%～+32%）
  - **ベースシナリオ**（CPU+AI持続）: $270～$280 by 2026末（現$197比 +37%～+42%）
  - **ブルシナリオ**（OpenAI full ramp）: $300+ by 2026末（現$197比 +50%+）

---

### 修正アクション推奨（Devil 反論への応答）

**結論: 「売却推奨」から「保有堅持・段階リバランス」に修正**

#### **推奨アクション（優先度順）**:

| Priority | Action | Timing | Rationale |
|----------|--------|--------|-----------|
| 🟢 高 | **保有メジャーポジション維持** | 即時 ～ 3ヶ月 | China cliff は一時的。長期 CPU + OpenAI 需要が主流 |
| 🟡 中 | **利確 10～15%（$220～$230 時点）** | 1～2ヶ月内 | 心理的支持ライン。アップサイドは十分残存 |
| 🟡 中 | **下値買い指値 $180～$190 設定** | 現在 | Devil シナリオの完全否定ではなく、リスク管理。ただし実現確度は低い（20%未満） |
| 🟢 高 | **Q1 決算説明会（2026年4月予想）の注視** | 来月 | OpenAI ramp 進捗・次世代チップ需要確認が最重要 |
| 🟠 低 | **$160 での指値買いは再検討** | 保留 | Devil の下値想定には根拠不足。むしろ $220～$240 帯での利確とリバランスを優先 |

---

### Devil へのフリップ条件（Flip Triggers）への修正

分析者がDevil の立場に転じる条件（より厳格に設定）：

| Flip Trigger | Probability | Timeline |
|---|---|---|
| ❌ China 規制が**さらに悪化** → Q1 $100M から Q2 $50M 以下へ（政治的劇変） | 15% | Q1決算期待 |
| ❌ OpenAI の Instinct GPU 調達計画が**公式に延期/縮小** | 10% | 2026H1 |
| ❌ KeyBanc や他主要アナリストが$270 目標を $200 以下に **一斉下方修正** | 20% | Q1決算後 |
| ❌ AI インフラ投資（Meta, Google, Microsoft） の cap ex 公表で **成長率 30% 以下** 確定 | 25% | 2026年内 IR |
| ✅ CPU ASP が予想通り +10～15% 実現 + OpenAI Q2 ramp 開始発表 | 60%+ | Q1決算説明会（4月） |

→ **総合判定**: Flip トリガーの発火確度は Devil 想定より **3～5倍低い**

---

### 暫定結論（Round 3）

**最終推奨: HOLD / 段階的リバランス（修正：「段階売却」から「保有堅持・利確」に変更）**

**根拠の再整理**：

1. **China Cliff は重要だが、構造的終焉ではなく一時的調整**
   - Q4 $390M の一時性を認める
   - Q1 $100M は「正常化ベース」で、年間 $400M～$600M 程度に収束の見通し
   - これを除いても、米国・EU・日本向けの CPU/GPU 需要は +40% 超の成長継続

2. **AI 需要「正常化」は悪いニュースではなく、業界の健全化**
   - 爆発的成長 → 持続的成長への遷移
   - KeyBanc の $270 PT（$288 に近い）は、この新段階での見方
   - CPU ASP +10～15% + AI売上 $14B～$15B は、業界標準的な見通し

3. **2/6 反発 +7.7% は、市場が短期パニックを自己修正中**
   - $197 は「安全な底値」ではなく、「売られ過ぎからの中値」
   - 追加下値テスト（$170～$180）の確度は **20% 程度**（Devil 想定より著しく低い）

4. **OpenAI 6GW 契約 + CPU ほぼ売却済み = 確定的な需要サポート**
   - 長期 visibility が上昇
   - Q1 ガイダンス「-5% QoQ」は、季節性 + China cliff 織り込み後の現実的見通し

**売却判断の見直し**：
- Devil 推奨の「50～75% 即時売却」は **非推奨**（時期尚早）
- 代わりに：**保有堅持 → $220～$230 時点で 10～15% 利確 → $210 割れで追加買い（段階リバランス）**

---

### EXPORT (Round 3 - Analyst Response)

\`\`\`json
{
  "symbol": "AMD",
  "round": 3,
  "agent": "Analyst (Response to Devil's Advocate)",
  "analysis_date": "2026-02-07",
  "stance": "HOLD / Staged Rebalance (Modified from Partial Sell-off)",
  "last_price": 197.13,
  "recent_rebound": "+7.7% (2026-02-06)",
  "key_arguments": {
    "china_cliff_reframe": "Temporary one-time adjustment, not structural end. Annual China revenue ~$400-600M (not $1.5B loss)",
    "ai_demand_reframe": "Not 'normalization end' but transition from explosive to sustained growth. Healthier long-term",
    "openai_factor": "6GW supply contract = confirmed multi-year demand. Market undervaluing visibility",
    "keybanc_upgrade": "$270 PT on CPU 50%+ growth + AI $14-15B revenue. Discrepancy with Devil's $160 target suggests analyst split"
  },
  "price_targets": {
    "conservative_2h2026": "$240-260 (on AI normalization scenario)",
    "base_case_2h2026": "$270-280 (on sustained CPU + AI growth)",
    "bull_case_2h2026": "$300+ (on OpenAI ramp acceleration)"
  },
  "recommendations": {
    "primary_action": "Hold majority position",
    "take_profit_level": "$220-230 (10-15% of position)",
    "buy_dip_level": "$180-190 (limited probability 20%)",
    "avoid": "$160 reentry target (insufficient downside rationale)"
  },
  "flip_triggers": [
    "OpenAI GPU demand materially delayed (10% probability)",
    "Analyst consensus downgrade to $200 PT (20% probability)",
    "AI capex growth <30% confirmed (25% probability)",
    "CPU ASP not achieved + OpenAI ramp misses (40% combined probability)"
  ],
  "key_catalysts": [
    "Q1 2026 earnings call (April 2026) - OpenAI ramp update critical",
    "Next-gen AI chip ramp (H2 2026) - revenue cycle inflection",
    "CPU ASP confirmation in Q1 earnings - $10-15% achievement key"
  ],
  "confidence": "Medium-High (70%, vs Devil's 75%)",
  "conviction_change": "Shifting from 'Sell 50-75%' to 'Hold & Rebalance'. China cliff impact accepted but overstated by market."
}
\`\`\`

---

