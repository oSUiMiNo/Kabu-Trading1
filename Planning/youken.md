Plan Agent 要件定義書（v0 / 固定値反映版）
1. 目的

議論プロジェクトが生成したログ（議論ログ・意見ログ・判定ログ）と、ユーザー入力（予算・リスク上限・保有状況・期間）をもとに、最終判定に沿った 実行可能なプラン（通知前提） を作成する。

自動売買（発注）はしない

後工程（Monitor / Task管理）が扱いやすいよう、機械可読な PlanSpec を出力する

プランには必ず 根拠（Fact/Source） を紐づけ、あとで検証できる状態にする

2. スコープ
2.1 v0でやること

最終判定（BUY / NO_BUY / SELL / NO_SELL）と票を受け取り、行動プランを生成

予算・リスク上限・期間・保有状況を反映して、**配分% / 投入額 / 株数（または購入単位）**を提案

価格ズレが大きい場合は 停止→再評価要求 に切り替える

根拠（fact_id/source_id）をプランに必ず保持する

日本株の 単元（100株）制約を考慮し、実行不可能な場合はその旨を明示

2.2 v0でやらないこと（非目的）

実売買（注文発注）、証券API接続

Web検索（最新価格/決算日取得はMarketData Agentに委譲）

ニュースの自動評価、ファンダメンタル再解釈

監視タスクの実行（頻度や監視ロジックはMonitorで実装）

3. 前提 / 関連コンポーネント

MarketData Agent（別）：最新価格・（任意で）イベント日付などを取得してPlan Agentに供給

Monitor Agent（別）：PlanSpecを定期チェックして通知（頻度は後で決める）

Task Manager Agent（別）：when表現のタスクをリマインド（後で作る）

4. 入力
4.1 必須入力

議論プロジェクトのログ一式

debate_log（Fact/Source/日付を含む）

opinion_log（買う側/買わない側等の要旨）

judge_log（最終判定＋票：例「買う6-買わない0」）

ユーザー入力

budget_total_jpy（総投資予算）※複数銘柄前提

risk_limit（許容損失：円 or %。v0は「1トレードあたり」で解釈）

horizon（SHORT / MID / LONG）

position_state（保有株数、平均取得単価、現金）

trading_constraint：現物のみ（固定）

4.2 MarketData Agentからの入力（実行直前更新）

current_price（最新価格）

anchor_price（議論時点の基準価格：ログ由来）

（任意）earnings_date 等（あれば Plan の注意書きに反映）

5. 固定ルール（v0確定値）
5.1 価格ズレ許容幅（アンカー価格比）

SHORT：±3%

MID：±5%

LONG：±7%

ブロック：±10%超 → 停止→再評価要求

abs(current_price - anchor_price) / anchor_price * 100 を price_deviation_pct とする。

5.2 ログの最大許容経過時間（鮮度）

SHORT：2日

MID：7日

LONG：30日

期限内でも以下は再評価推奨（plan内に明記）

価格ズレがブロック（±10%超）

ログの根拠がイベント依存で、イベント日が近い/過ぎた（任意入力がある場合）

5.3 損切り幅（期間別デフォルト）

SHORT：-4%

MID：-8%

LONG：-15%

共通：-20%より深い損切りは採用しない（上限）

5.4 予算配分の基本ルール（複数銘柄前提）

1銘柄 最大配分%：10%

（任意拡張）confidence=HIGH のときのみ 15%まで許可

1銘柄 最小配分%：2%

現金比率下限：25%（常に残す）

5.5 票差 → confidence のルール

p = for / (for + against) とする

HIGH：p ≥ 0.83（例：6-0, 5-1）

MED：0.67 ≤ p < 0.83（例：4-2）

LOW：p < 0.67（例：3-2）

5.6 confidence → 配分倍率（提案%の強さ）

HIGH：最大配分の 1.0 倍

MED：最大配分の 0.6 倍

LOW：最大配分の 0.3 倍

例：最大10%なら、HIGH=8〜10%、MED=4〜6%、LOW=2〜3% を目安に提案。

5.7 日本株の最小購入単位（100株）の扱い（現物）

計算された株数を 100株単位に切り捨て

shares = floor(shares/100)*100

shares == 0 の場合、100株を買えるかを判定

100株が予算・配分上限・リスク上限に収まらない場合は

status = NOT_EXECUTABLE_DUE_TO_LOT として出力

v0では「見送り（NO_BUY相当のプラン）」または「監視のみ」を提案

代替案（将来拡張の注記）：単元未満株/ETF/投信

6. 処理仕様（v0）
6.1 高レベルフロー

ログから最終判定と票を抽出

MarketData入力（current_price）を受け取る（実行直前）

鮮度チェック（ログ経過日数）→ 古い場合は再評価推奨フラグ

価格ズレ判定（許容内/ブロック）

判定別にプラン生成

BUY：新規 or 買い増し（position_stateで分岐）

SELL：全売りを基本（v0は簡略化）

NO_BUY / NO_SELL：監視方針（強/中/弱）を決める

PlanSpec（機械可読）として出力

6.2 BUY / SELL のブロック挙動

価格ズレが ±10%超 の場合：

data_checks.status = BLOCK_REEVALUATE

プランは「停止→再評価要求（数量確定しない）」に切り替える

7. 出力（PlanSpec）
7.1 出力フォーマット（YAML例）
ticker: "{TICKER}"
plan_id: "{YYYYMMDD}-{TICKER}-{SEQ}"

decision:
  final: "BUY|NO_BUY|SELL|NO_SELL"
  vote:
    for: 0
    against: 0
  horizon: "SHORT|MID|LONG"
  p: 0.0
  confidence: "HIGH|MED|LOW"
  decision_basis:
    - fact_id: "F12"
      source_id: "S3"
      why_it_matters: "結論の決め手"

freshness:
  log_age_days: 0
  max_allowed_days: 0
  status: "OK|STALE_REEVALUATE"

data_checks:
  anchor_price: 0
  current_price: 0
  price_deviation_pct: 0
  deviation_ok_pct: 0      # SHORT=3 / MID=5 / LONG=7
  deviation_block_pct: 10
  status: "OK|BLOCK_REEVALUATE"

risk_defaults:
  stop_loss_pct: 0         # SHORT=-4 / MID=-8 / LONG=-15
  stop_loss_cap_pct: -20

allocation_policy:
  max_pct: 10
  min_pct: 2
  cash_min_pct: 25
  confidence_multiplier: 1.0   # HIGH=1.0, MED=0.6, LOW=0.3

portfolio_plan:
  budget_total_jpy: 0
  cash_reserved_jpy: 0
  allocation_pct: 0
  allocation_jpy: 0
  instrument_lot:
    market: "JP|US"
    lot_size: 100
    lot_policy: "FLOOR_TO_LOT"
  quantity: 0
  status: "OK|NOT_EXECUTABLE_DUE_TO_LOT"

execution_plan:
  order_style: "ONE_SHOT"
  entry_rule: "NEXT_BUSINESS_DAY_OPEN"
  notes:
    - "自動発注はしない。通知とプラン提示のみ。"

monitoring_hint:
  intensity: "STRONG|NORMAL|LIGHT"
  reason: "vote_gap/confidence/freshness等"

7.2 intensity（監視方針）だけはPlan側で出す

HIGH → STRONG

MED → NORMAL

LOW → LIGHT
（監視頻度の具体数値は Monitor 実装時に決める）

8. 受け入れ基準（v0）

BUY/SELL/NO_BUY/NO_SELL の全ケースで PlanSpecを必ず出力

価格ズレが ±10%超の場合、必ず BLOCK_REEVALUATE に切り替わる

出力に根拠（fact_id/source_id）が含まれて追跡可能

日本株の100株制約により実行不能な場合、NOT_EXECUTABLE_DUE_TO_LOT を明示して破綻しない

自動発注処理を含まない

9. 未決事項（v0では決めない）

監視頻度・監視ロジック（Monitor Agent側で後で設計）

重要イベント（決算等）の詳細ルール（入力が増えたら強化）