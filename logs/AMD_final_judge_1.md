# Final Judge Log: AMD

## Inputs (discovered)

- **target_sets**: [set1, set2]（opinionが一致したセットのみ、両セットとも合意済み）
- **対象セット数**: 2
- **opinion agreement status**:
  - set1: ✅ AGREED（両opinion → NOT_BUY_WAIT）
  - set2: ✅ AGREED（両opinion → BUY）

### 元ログとjudgeファイル
| セット | 元ログ | judge | 判定 |
|--------|--------|-------|------|
| set1 | AMD_set1.md | AMD_set1_judge_1.md | NOT_BUY_WAIT |
| set2 | AMD_set2.md | AMD_set2_judge_1.md | BUY |

---

## Per-set decisions

### set1（グロスマージン圧力・Intel脅威重視視点）
- **supported_side**: NOT_BUY_WAIT
- **one_liner**: "グロスマージン -11pp 低下（Q4 57%→Q1見通し46%）と Intel Granite Rapids Xeon 6900P のパリティ達成により、Q1決算（2026年5月5日）の実績確認まで待機判定"
- **notes**:
  - 主要根拠：F11（グロスマージン低下）、F13（Intel パリティ達成）
  - Analyst の段階的リスク修正が Devil の単一視点を上回った
  - 両opinion共通：「一時的か構造的か未確定」が判定の理由
  - 決定トリガー：Q1決算で GM 46% 実績確認後、YoY 成長失速時に売却判定へ

---

### set2（MI400採用確度向上・リターン非対称性反転視点）
- **supported_side**: BUY
- **one_liner**: "CES 2026 公表による MI400採用確定（Meta・AWS・Microsoft）と、17%急落が『地政学的調整』と判定されたため、リスク・リターン非対称性が売却→保有有利へ反転。期待値 +28%"
- **notes**:
  - 主要根拠：S9-S13（Round 3追加ソース）、F2-Analyst（China MI308特需終焉の解釈）
  - Devil の flip_trigger「MI400採用が複数hyperscaler確認」が既に実現
  - 両opinion共通：Bull確度 85%、期待値 +28%（修正後）
  - 下側リスク：-15%～-20%（採用確定後で防衛強化）

---

## Final Decision

### 最終判定サマリー
- **supported_side_display**: **NOT_BUY (WAIT)** ← 安全側をベース
- **supported_side_machine**: NOT_BUY_WAIT
- **overall_agreement**: **MIXED** ← set1 vs set2 が相反（NOT_BUY_WAIT vs BUY）

### 結論理由

**1. 対立の本質**
- set1：マージン圧力とIntel脅威を重視 → **短期判定留保（待機）**
- set2：MI400採用確度とリターン反転を重視 → **即座の買い継続**
- 同じ銘柄・同じ時期の分析ながら、**新情報（CES 2026）の評価の相違**が判定分岐を生成

**2. 判定の分裂理由**
| 視点 | set1の立場 | set2の立場 | データソース |
|------|-----------|-----------|-----------|
| **グロスマージン -11pp** | リスク大・構造的懸念 | 一時的・初期段階コスト | F11（S10） |
| **Intel 脅威レベル** | 高い（Diamond Rapids H2リスク） | 限定的（Custom ASICは補完） | F13（S12-S13） |
| **MI400採用状況** | 未確定・Q1決算待機 | **既に確定（Meta/AWS/Microsoft）** | S9（CES 2026公表） |
| **17%急落の解釈** | 需要減速・調整の開始 | 地政学＋China特需終焉 | F2-Analyst（S11） |

**3. 矛盾の理由**
- set1は **round 3 の新ソース（S10-S13）が反映される前の** 議論ベース
- set2は **CES 2026 公表（2月直前）の最新情報を含む** 議論ベース
  - → つまり set2が「より新しいデータを持つ版」

**4. 時系列の位置**
```
Round 1-2:    基本的な決算・市場分析
      ↓
Round 3:      CES 2026 公表 + 新ソース（S9-S13）
      ↓
set1:         Round 3後も「待機」推奨（マージン・Intel脅威を最優先）
set2:         Round 3の新情報を全面採用 → 「買い」推奨
```

**5. 安全側の判定**
- set1の「NOT_BUY_WAIT」（Q1決算まで待機）を **最終判定として採用**
- 理由：
  1. グロスマージン 46% は **新規ガイダンス値で確定的**（予想ではなく、既に決算で予告済み）
  2. Intel Diamond Rapids（H2 2026）脅威は **段階的に顕在化する懸念**
  3. set2 の「既に採用確定」はpublic announcement だが、**実際の shipment / revenue 計上は5月Q1決算まで待たねば確認不可**
  4. リスク・リターンが「非対称性反転」と判定されても、**データの完全性（shipment rate実績）がQ1決算後まで不足**

---

## Conflicts (MIXED判定の根拠)

1. **グロスマージン -11pp の構造性評価**
   - set1: 「短期戦略（価格でシェア）が3～5年で回復」は前提条件として脆弱（F12の公式説は後付け理由の可能性）
   - set2: 「MI350初期段階コスト」で一時的と判定、規模の経済効果で回復可能
   - **矛盾**: 同じF12（AMD公式）を「前提脆弱」と「根拠確実」で評価が分岐

2. **Intel 脅威の時間軸評価**
   - set1: Diamond Rapids H2（データセンター主流）で「脅威高い」と判定。Q2-Q3成長失速リスク
   - set2: Hyperscaler自社ASIC開発でNVIDIA支配が瓦解 → AMD「唯一実行可能代替案」の地位強化。Intel脅威は限定的
   - **矛盾**: Intel脅威の重要性評価が 2倍以上異なる

3. **新情報（CES 2026公表）の評価タイミング**
   - set1: MI400採用公表は「initial order」段階であり、revenue計上・margin確認は5月決算が必須
   - set2: 採用確定＝flip_trigger発動 → 即座の買い継続判定
   - **矛盾**: 同じpublic announcement を「前提検証待ち」 vs 「結論確定」で評価

4. **リスク・リターン非対称性の確度評価**
   - set1: Bull確度85%は「採用確定後の確度」だが、実績validation待機が適切
   - set2: Bull確度85% → 即座に行動判定へ
   - **矛盾**: 同じ数値から導かれる行動判定が逆向き

---

## Next things to clarify (優先順)

1. **Q1 2026決算での MI400 shipment rate 実績発表（2026年5月5日予定）**
   - set1の「待機判定」がこの決算で否定 or 肯定されるかが全判定の鍵
   - 具体的には：
     - MI400月間 shipment が計画比 +20%以上達成か？
     - 契約額（revenue）計上が Q1 時点で明確化されるか？

2. **グロスマージン 46% が Q1実績で確定するかどうか**
   - ガイダンス 46% ± 1.5% で、実績が 47.5%超なら「一時的」がより確度向上
   - 実績 44.5%以下なら「構造的」の可能性が高まり、set1の懸念が強化

3. **Intel Diamond Rapids（Xeon 6900P）の実際の顧客採用状況**
   - CES 2026 以降の顧客発表状況（Google/Meta が Intel も採用するか？）
   - set2の「脅威限定的」評価が維持されるか確認必須

4. **Hyperscaler capex計画の詳細開示**
   - Q1決算時に Google/Meta/AWS が 2026年下期の capex見通しを明確化するか？
   - set2の「Data Center本体は sequential up」評価の確認材料

5. **NVIDIA の次期GPU（Blackwell超）や新戦略の発表**
   - Q1-Q2期間の NVIDIA のアナウンスメント（set2で言及の「競争環境動向」）
   - AMD の「唯一実行可能代替案」の地位が堅牢か確認

---

## Rationale (最終判定を支持する理由)

**NOT_BUY_WAIT を選定した根拠（3～6個）：**

1. **グロスマージン -11pp は新規ガイダンス値で確定的**
   - Q4 57% → Q1 46% は決算予告済み。set2の「一時的」は希望観に基づく
   - EPS成長の最重要変数であり、実績確認まで judgment留保が適切

2. **Intel Granite Rapids Xeon 6900P のパリティ達成は脅威レベル高い**
   - 128コアの完全同等化で、Q2-Q3の顧客購買検討が Intel へシフトする可能性
   - set2の「Custom ASIC補完論」は理論的だが、Data Center 購買実績で証明されていない

3. **MI400採用確定は『public announcement』であり『revenue計上』ではない**
   - set2が引用する S9（CES 2026）は採用意思表示であり、実際の shipment / revenue は5月決算まで不確定
   - 過去のAI産業の「announces vs 実績」ギャップを勘案すれば、確認待機が合理的

4. **リスク・リターン非対称性は「前提条件」に依存**
   - set2の「Bull確度85%」は「MI400採用確定＆margin持続」を前提
   - これらが5月決算で検証されるまで、numerator（期待値 +28%）の信頼度が低い

5. **Q1決算（5月5日）までの残存時間は約3ヶ月で短い**
   - set1の「待機判定」は決定的な新情報（決算実績）を待つ3ヶ月
   - リスク・リターンの判断を delay することの cost（機会喪失）は小さい

6. **set1とset2の「判定分裂」自体が『確実性の不足』を示唆**
   - 同じ銘柄・同じ時期で両立不可の判定が出ることは、市場環境の不確実性が高い状態
   - この場合、保留型判定（NOT_BUY_WAIT）が prudent

---

## EXPORT（yaml）

```yaml
銘柄: AMD
最終判定番号: 1
判定日時: 2026-02-07
判定ステータス: MIXED（set1とset2の相反）

入力:
  対象セット: [1, 2]
  元ログ:
    set1: "AMD_set1.md"
    set2: "AMD_set2.md"
  判定ソース:
    set1: "AMD_set1_judge_1.md（NOT_BUY_WAIT - 両opinion一致）"
    set2: "AMD_set2_judge_1.md（BUY - 両opinion一致）"

セット別結果:
  set1:
    支持側: NOT_BUY_WAIT
    一行要約: "グロスマージン -11pp + Intel パリティで Q1決算まで待機"
    opinion一致度: AGREED

  set2:
    支持側: BUY
    一行要約: "CES 2026公表によりMI400採用確定・期待値+28%で即座の買い継続"
    opinion一致度: AGREED

最終判定:
  支持側: NOT_BUY_WAIT
  支持側_機械用: NOT_BUY_WAIT
  総合一致度: MIXED

  判定理由:
    - "グロスマージン -11pp は新規ガイダンス値で確定的・実績待機が適切"
    - "Intel Granite Rapids Xeon 6900P パリティ達成は脅威レベル高い"
    - "MI400採用確定は公表であり revenue計上ではない・5月決算待機が合理的"
    - "リスク・リターン非対称性は前提条件に依存・検証待機が必要"
    - "Q1決算までの3ヶ月は短く、待機判定の opportunity cost は小さい"
    - "set1とset2の相反が『確実性の不足』を示唆・保留型判定が prudent"

対立点:
  - "グロスマージン -11pp の『構造性』評価：set1は脆弱、set2は一時的"
  - "Intel 脅威の重要度：set1は高い（H2リスク）、set2は限定的（ASIC補完）"
  - "新情報（CES 2026）の判定への組入：set1は待機、set2は即座に判定確定"
  - "リスク・リターン非対称性の『確度』：set1は前提検証待機、set2は即座行動"

次に明確化（優先順）:
  1: "Q1 2026決算（5月5日）での MI400 shipment rate と revenue計上確認"
  2: "グロスマージン 46% が実績で確定するか・一時的か構造的か判定"
  3: "Intel Diamond Rapids 顧客採用状況・set2の『脅威限定的』評価の妥当性"
  4: "Hyperscaler capex計画詳細・『Data Center sequential up』の持続性"
  5: "NVIDIA 次期製品・AMD『唯一実行可能代替案』地位の堅牢性"

データ制限:
  - "set1と set2 の元ログが異なる情報ベース・時期の相違を反映"
  - "CES 2026公表は public announcement だが、実際の shipment/revenue計上は未実績"
  - "MI400採用企業（Meta/AWS/Microsoft）の詳細 shipment plan は非公開の可能性"
  - "グロスマージン見通し±1.5% の幅があり、実績のばらつき予想"

判定の確実性:
  概要: "set1とset2の相反により、最終判定の信頼度は中程度。5月Q1決算が鍵"
  信頼度: 58% （MIXED判定特有の低さ。set1単独なら68%、set2単独なら75%）
  推奨: "NOT_BUY_WAIT（保留型）での実施が prudent。ただし set2の見立てが正確な場合、3ヶ月の機会喪失可能性あり"
```

---

## 補足：判定の判断方法

**複数セットでの判定が相反した場合の集約ルール：**

| シナリオ | 最終判定 | 理由 |
|--------|--------|------|
| 全セット一致 | その支持側 | 合意が強い |
| N/2 以上が同じ | その支持側（多数決） | 多数派 |
| 完全二分（set1 vs set2） | NOT_BUY_WAIT（安全側） | **本ケース**：投資判断は prudent に |

**本ケースの適用：**
- set1（NOT_BUY_WAIT）と set2（BUY）の 1:1 分裂
- 上値リスク（set2）vs 下値リスク（set1）が相反
- 決定的な実績データ（Q1決算）が3ヶ月後という条件
- → **安全側の NOT_BUY_WAIT を採用** ← 投資判定の prudence 原則

