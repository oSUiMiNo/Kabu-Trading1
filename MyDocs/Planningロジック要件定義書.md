# Planning ロジック要件定義書：ポジションサイジング & リスクリワード比

---

## 1. 概要

### 目的

Planning ブロックにポジションサイジングとリスクリワード比（RR比）の計算を組み込む。
3者議論（Claude/ChatGPT/Codex）で「指標ではなく必須ルール」として Planning に固定すると結論された2つの資金管理ロジック。

設計方針は「Planningロジック（資金管理ルール）.md」を参照。

### 現状の問題

現在の Planning（`plan_calc.py`）は**配分計算**（予算 × confidence別 % → 投入額 → 株数）を行っているが、以下が欠けている：

1. **ポジションサイジング**：「損失を資金の何%に抑えるか」から投入上限を逆算するロジックがない。現在は confidence 別の配分%で投入額を決めているが、**損切りラインまでの距離を考慮していない**
2. **リスクリワード比**：利確と損切りの比率を評価するロジックがない。RR比が1未満（損大利小）のトレードも通ってしまう

---

## 2. 追加するロジック

### 2.1 ポジションサイジング

**考え方**：「1回のトレードで失ってよい金額」を先に決め、そこから投入上限を逆算する。

```
入力：
  total_budget_jpy     = 3,000,000円（総予算）
  risk_limit_pct       = 5%（1銘柄あたりの最大損失許容）→ 150,000円
  stop_loss_pct        = -8%（中期の損切り幅）

計算：
  max_loss_jpy         = total_budget_jpy × risk_limit_pct / 100
                       = 3,000,000 × 5% = 150,000円
  position_size_jpy    = max_loss_jpy / abs(stop_loss_pct / 100)
                       = 150,000 / 0.08 = 1,875,000円

意味：
  187.5万円分買えば、8%下がったとき損失は15万円（資金の5%）に収まる
```

**既存ロジックとの関係**：

現在の `calc_allocation()` は `budget × max_allocation_pct` で投入額を計算している。ポジションサイジングで計算した `position_size_jpy` と比較し、**小さい方を採用する**。

```
投入額 = min(
    budget × max_allocation_pct,     ← 既存（confidence ベース）
    max_loss_jpy / abs(stop_loss_pct) ← 新規（損失許容ベース）
)
```

### 2.2 リスクリワード比（RR比）

**考え方**：「得するかもしれない額」÷「損するかもしれない額」を計算し、1未満なら実行しない。

```
入力：
  current_price        = 170ドル
  stop_loss_pct        = -8%   → 損切り価格 = 170 × 0.92 = 156.4ドル
  take_profit_pct      = 20%   → 利確価格   = 170 × 1.20 = 204ドル

計算：
  potential_loss       = 170 - 156.4 = 13.6ドル
  potential_gain       = 204 - 170   = 34ドル
  rr_ratio             = potential_gain / potential_loss
                       = 34 / 13.6 = 2.5

判定：
  rr_ratio >= 1.0 → OK（実行可能）
  rr_ratio <  1.0 → NG（プランに警告を付与し、quantity=0 で実行不可にする）
```

---

## 3. 必要な設定値

### 既存の設定値（portfolio_config に存在）

| 設定 | 現在のキー | 現在の値 | 用途 |
|------|-----------|---------|------|
| 総予算 | `total_budget_jpy` | 3,000,000 | ポジションサイジングの基盤 |
| 損失許容% | `risk_limit_pct` | 5.00 | 1銘柄あたりの最大損失許容 |
| 損切り幅 | `stop_loss_pct` | {short:-4, mid:-8, long:-15} | 損切りラインの距離 |
| 利確ライン | `default_take_profit_pct` | 20.00 | RR比の計算に使用 |

### 新規追加が必要な設定値

| 設定 | 推奨キー | デフォルト値 | 用途 |
|------|---------|------------|------|
| 最低RR比 | `min_rr_ratio` | 1.0 | この比率未満のトレードは実行しない |

> `default_take_profit_pct` は portfolio_config に既に存在するが、`plan_calc.py` の `PlanConfig` にはまだ読み込まれていない。

---

## 4. 変更対象ファイル

### Planning/src/plan_calc.py

| 変更 | 内容 |
|------|------|
| `PlanConfig` に追加 | `default_take_profit_pct`, `min_rr_ratio` フィールド |
| `load_plan_config()` に追加 | DB から上記を読み込み |
| 新規関数 `calc_position_size()` | 損失許容額から投入上限を逆算 |
| 新規関数 `calc_rr_ratio()` | RR比を計算し OK/NG を判定 |
| `calc_allocation()` を修正 | ポジションサイジングの結果で投入額を制限 |

### Planning/src/plan_spec.py

| 変更 | 内容 |
|------|------|
| `PlanSpec` に追加 | `position_size_jpy`, `rr_ratio`, `rr_status` フィールド |
| `build_yaml()` に追加 | `risk_management` ブロックを YAML 出力に追加 |

### Planning/src/main.py

| 変更 | 内容 |
|------|------|
| `run_plan()` に追加 | ポジションサイジング計算 → RR比計算 → 結果を PlanSpec に反映 |
| RR比 NG 時 | `quantity=0`, 警告メッセージを `execution_notes` に追加 |

### config/portfolio_config.yml

| 変更 | 内容 |
|------|------|
| 追加 | `min_rr_ratio: 1.0` |

---

## 5. 出力フォーマット（PlanSpec YAML への追加）

現在の YAML に `risk_management` ブロックを追加する。

```yaml
risk_management:
  # ポジションサイジング
  max_loss_jpy: 150000           # 許容損失額 = budget × risk_limit_pct
  position_size_jpy: 1875000     # 投入上限 = max_loss_jpy / abs(stop_loss_pct)
  position_size_limited: true    # true = ポジションサイジングで投入額が制限された

  # リスクリワード比
  stop_loss_pct: -8.0
  take_profit_pct: 20.0
  rr_ratio: 2.5                  # 利確幅 / 損切り幅
  min_rr_ratio: 1.0
  rr_status: OK                  # OK or RR_TOO_LOW
```

**RR比が NG の場合：**

```yaml
risk_management:
  max_loss_jpy: 150000
  position_size_jpy: 1875000
  position_size_limited: false
  stop_loss_pct: -8.0
  take_profit_pct: 5.0           # 利確が小さい
  rr_ratio: 0.625                # 5 / 8 = 0.625 < 1.0
  min_rr_ratio: 1.0
  rr_status: RR_TOO_LOW          # ← NG

portfolio_plan:
  quantity: 0                    # 実行不可
  status: RR_TOO_LOW             # ← NG 理由

execution_plan:
  notes:
    - "RR比 0.63 < 1.0: 期待値がマイナスのため実行不可。利確ラインの見直しが必要。"
```

---

## 6. 計算フローの全体像（修正後）

```
現在のフロー：
  1. DB からセッション取得
  2. 鮮度チェック
  3. 価格ズレ判定
  4. confidence 算出
  5. 配分・株数計算 ← confidence × budget で投入額を決定
  6. PlanSpec 組立
  7. commentary 生成
  8. YAML 保存

修正後のフロー：
  1. DB からセッション取得
  2. 鮮度チェック
  3. 価格ズレ判定
  4. confidence 算出
  5. ポジションサイジング ← 【新規】損失許容から投入上限を逆算
  6. 配分・株数計算 ← 既存の配分額とポジションサイジングの小さい方を採用
  7. RR比計算 ← 【新規】利確/損切りの比率を評価、NG なら quantity=0
  8. PlanSpec 組立（risk_management ブロック追加）
  9. commentary 生成
  10. YAML 保存
```

---

## 7. エラー・エッジケース

| ケース | 対処 |
|--------|------|
| stop_loss_pct が 0 | ポジションサイジング計算不可。配分計算のみで投入額を決定（現状維持） |
| take_profit_pct が未設定 | `default_take_profit_pct`（DB or デフォルト20%）を使用 |
| RR比が 1.0 未満 | `quantity=0`, `portfolio_status="RR_TOO_LOW"`, execution_notes に警告追加 |
| ポジションサイジングの結果が配分計算より大きい | 配分計算の結果を採用（制限されない）。`position_size_limited=false` |
| BLOCK_REEVALUATE（価格ズレ超過） | 既存ロジックが先に quantity=0 にする。RR比計算は実行するが結果は参考値 |

### Discord 通知

エラー発生時は Discord に通知する。同じエラー種別は**1日に1回**だけ通知し、同日中の同種エラーは抑制する。

| エラー種別 | 通知 | 理由 |
|-----------|------|------|
| stop_loss_pct が 0 | 1日1回 | 設定ミス。直すまで全銘柄で同じエラーが出続けるため、銘柄ごとに通知すると大量になる |
| RR比が 1.0 未満 | 銘柄ごとに1日1回 | 銘柄固有の状況。同じ銘柄が同日に2回 Planning に来ることはほぼないため実質1通 |
| BLOCK_REEVALUATE | 銘柄ごとに1日1回 | 同上 |

**抑制の仕組み**：通知送信時にエラー種別と日付を記録し、同日中の同種エラーはスキップする。

---

## 8. 実装ステップ（案）

| # | 内容 | 備考 |
|---|------|------|
| 1 | `config/portfolio_config.yml` に `min_rr_ratio: 1.0` 追加 + DB 同期 | |
| 2 | `plan_calc.py`: `PlanConfig` に `default_take_profit_pct`, `min_rr_ratio` 追加 | `load_plan_config()` も更新 |
| 3 | `plan_calc.py`: `calc_position_size()` 実装 | |
| 4 | `plan_calc.py`: `calc_rr_ratio()` 実装 | |
| 5 | `plan_calc.py`: `calc_allocation()` にポジションサイジング制限を統合 | |
| 6 | `plan_spec.py`: `PlanSpec` に `risk_management` フィールド追加 + YAML 出力更新 | |
| 7 | `main.py`: `run_plan()` にポジションサイジング・RR比計算ステップを追加 | |
| 8 | `main.py`: commentary エージェントのプロンプトに重要指標データを注入 | archive.important_indicators を読み取って渡す |
| 9 | テスト | 正常系 + RR比NG + ポジションサイジング制限のケース |

---

## 9. 重要指標の Planning での扱い

### commentary エージェントへのデータ注入（本スコープ）

archive.important_indicators のデータを commentary エージェント（plan-generator.md）のプロンプトに注入する。エージェントは EventRisk（イベント接近度）、MarketRegime（VIX）、金利動向等を参照し、execution_notes や monitoring_hint.reason に状況に応じたコメントを生成する。

### 数値計算への反映（スコープ外 → Issue #132）

EventRisk・MarketRegime を数値計算（ポジションサイズ調整等）に反映する具体的なルールは未定義。3者議論では渡し先に Planning が含まれていたが、閾値や計算式は議論されていない。具体的なルールが決まり次第 plan_calc.py に組み込む。

---

## 10. スコープ外

- EventRisk・MarketRegime の数値計算への反映（Issue #132）
- ドル出来高に応じたポジションサイズ調整（Issue #130 の Liquidity 関連）
- トレーリングストップなどの動的な損切りルール
- 複数銘柄間の相関を考慮したポートフォリオレベルのリスク管理
