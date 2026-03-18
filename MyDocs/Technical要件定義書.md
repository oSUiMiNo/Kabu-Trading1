# Technical ブロック 要件定義書

> Issue #11：テクニカル情報をとるAPI

---

## 1. 概要

### 目的

株価のテクニカル指標（移動平均、RSI、MACD、ボリンジャーバンドなど）を自動取得し、archive テーブルに記録する**大ブロック**を新設する。
Monitor はこのデータを読んでからチェックを行い、Discussion・Planning も定量データを参照できるようになる。

### 利用ライブラリ

[TechnicalIndicatorFetcher](https://github.com/oSUiMiNo/TechnicalIndicatorFetcher.git)（自作ライブラリ）

- yfinance 経由で OHLCV を取得し、22種以上のテクニカル指標 + ローソク足パターン + 機械判定ラベル（derived states）を算出
- 外部 API キー不要（Yahoo Finance は無料）
- Python >=3.12、numpy / pandas / yfinance のみに依存（TA-Lib 不要）

---

## 2. パイプライン上の位置づけ

### 従来のパイプライン

```
Monitor → Discussion → Planning → Watch
          （NG のみ）
```

- Monitor が NG 判定時のみ archive レコードを作成
- Discussion は自分用の別の archive レコードを作成
- `propagate_active_after_discussion()` で Monitor の archive から Discussion の archive にデータをコピーしていた

### 新パイプライン

```
Technical → Monitor → Discussion → Planning → Watch
                      （NG のみ）
```

**最大の変更点：全ブロックが同一の archive レコードに書き足す**

1つの銘柄に対して1つの archive レコードを Technical が作成し、後続ブロックはそこに自分の担当カラムを書き足していく。ブロックごとに別レコードを作る従来方式を廃止する。

```
archive ID: 20260319100000_AAPL（1レコード）
  Technical  → technical を書き込み
  Monitor    → monitor, MotivationID, active を書き込み
  Discussion → lanes, final_judge を書き込み
  Planning   → newplan_full, verdict を書き込み
  Watch      → watchlist 更新 + active=False
```

これにより `propagate_active_after_discussion()` は不要になる。

### フロー詳細

```
Phase 1: Technical
  └─ watchlist の全銘柄に対して archive レコードを作成
  └─ テクニカル指標を取得 → archive.technical に書き込み

Phase 2: Monitor
  └─ Technical が作成した archive を取得（monitor IS NULL）
  └─ archive.technical を参照しつつ各銘柄をチェック
  └─ 結果を同じ archive の monitor に書き込み
  └─ NG → MotivationID=1, active=True
  └─ OK → MotivationID=0, active=False, status=completed

Phase 3: Discussion（NG 銘柄のみ）
  └─ active=True AND final_judge IS NULL の archive を取得
  └─ 同じ archive の lanes, final_judge に書き込み

Phase 4: Planning
  └─ 同じ archive の newplan_full, verdict に書き込み

Phase 5: Watch
  └─ watchlist 更新 + archive.active=False
```

### Discussion 単体実行（パイプライン経由でない場合）

```
Discussion 開始
  └─ active=True AND final_judge IS NULL の archive を探す
       └─ 見つかった → その archive を使う
       └─ 見つからない → archive を新規作成
  └─ ensure_technical_data(archive_id) ← ガード関数
       └─ archive.technical が空 → Technical を呼んで取得・記録
       └─ archive.technical が既にある → 何もしない
  └─ その archive に lanes, final_judge を書き込み
```

---

## 3. ブロック構成

### 配置

```
Technical/
├── src/
│   ├── technical_orchestrator.py   # エントリーポイント
│   └── ...
├── pyproject.toml                  # uv で依存管理
└── .venv/                          # 独立した仮想環境
```

### 依存パッケージ

```
technical-indicator-fetcher   # git+https://github.com/oSUiMiNo/TechnicalIndicatorFetcher.git
yfinance                      # TechnicalIndicatorFetcher の依存（自動インストール）
numpy, pandas                 # 同上
```

※ Technical ブロックの venv にのみインストールし、他ブロックの環境を汚さない。

---

## 4. 機能仕様

### 4.1 コア機能：テクニカル指標取得 & DB 記録

**実行モード**

| モード | コマンド例 | 用途 |
|--------|-----------|------|
| 全銘柄一括 | `python technical_orchestrator.py` | パイプライン Phase 1 |
| 単一銘柄 | `python technical_orchestrator.py --ticker AAPL` | ガード関数からの呼び出し |

**処理フロー（全銘柄一括モード）**

```
1. watchlist から active な銘柄一覧を取得
2. 各銘柄を並列で処理：
   a. archive レコードを作成（create_archivelog）
   b. 設定読み込み（timeframes / period 等）
   c. 時間足ごとに TechnicalIndicatorFetcher を実行
      └─ fetch_and_run_with_yfinance(symbol, timeframe, period, interval, ...)
   d. 結果を統合
   e. archive.technical に書き込み
```

**処理フロー（単一銘柄モード）**

```
1. archive テーブルから対象レコードを検出（ticker=指定値 AND technical IS NULL）
2. テクニカル指標を取得
3. archive.technical に書き込み
```

**出力データ構造（archive.technical に格納）**

```jsonc
{
  "fetched_at": "2026-03-18T10:30:00+09:00",
  "timeframes": {
    "1d": {
      "schema_version": "1.0",
      "symbol": "AAPL",
      "timeframe": "1d",
      "as_of": "2026-03-17T00:00:00",
      "data_summary": {
        "bars_used": 260,
        "latest_close": 175.50,
        "latest_volume": 52000000
        // ...
      },
      "indicators": {
        "raw": {
          "sma_20": 172.3,
          "sma_50": 168.1,
          "sma_200": 160.5,
          "rsi_14": 55.3,
          "macd": { "macd": 1.2, "signal": 0.8, "hist": 0.4 }
          // ... 22種以上
        },
        "derived": {
          "trend": { "close_vs_sma20": "above", "adx_state": "trend_present" },
          "momentum": { "rsi_state": "neutral", "macd_histogram_sign": "positive" },
          "volatility": { "bbands_position": "inside_upper_half" },
          "volume": { "obv_direction_5": "up", "mfi_state": "neutral" }
        }
      },
      "candlestick_patterns": {
        "latest_bar": { },
        "recent_hits": [ ]
      },
      "warnings": [ ]
    }
    // 複数時間足を取得する場合はここに並ぶ
  }
}
```

### 4.2 ガード関数：ensure_technical_data

Discussion を単体実行する場合など、Technical フェーズを経由していない状況に対応する。

**配置先**：`shared/supabase_client.py`

```python
def ensure_technical_data(archive_id: str) -> bool:
    """
    archive.technical が未取得なら Technical ブロックを呼び出して取得する。
    既に取得済みなら何もしない。

    Returns: True（データあり or 取得成功）/ False（取得失敗）
    """
```

**ロジック**

```
1. archive_id で archive レコードを取得
2. technical が既にある → return True（何もしない）
3. technical が空 →
   a. ticker を取得
   b. Technical ブロックを subprocess で呼び出し（--ticker <ticker>）
      Technical が DB から対象レコードを自動検出して書き込む
   c. 成功 → return True / 失敗 → return False
```

---

## 5. 既存ブロックの仕様変更

### 5.1 Monitor

#### 実装済み：全結果を archive に記録

| 項目 | 従来 | 現在（実装済み） |
|------|------|-----------------|
| archive レコード作成 | NG 時のみ | **全結果（OK / NG / ERROR）で作成** |
| MotivationID | NG 時のみ 1 をセット | NG=1 / OK・ERROR=0 |
| active フラグ | NG 時のみ True | NG=True / OK・ERROR=False |
| status | NG: running | NG: running / OK: completed / ERROR: error |

#### 未実装：Technical ブロック完成後の追加変更

| 項目 | 現在 | Technical 完成後 |
|------|------|-----------------|
| archive レコード作成 | Monitor 自身が作成 | Technical が事前作成済みのレコードを使う（Monitor は作成しない） |
| テクニカルデータ参照 | なし | archive.technical を読み取ってチェックに活用 |

### 5.2 Discussion

#### 未実装：同一 archive への書き込み

| 項目 | 現在 | 変更後 |
|------|------|--------|
| archive レコード | Discussion が自分用の新レコードを作成 | Monitor が書き込んだ既存レコードに書き足す |
| propagate | Discussion 後に monitor 等をコピー | 不要（同一レコードなので最初からデータがある） |

**変更後の Discussion 処理フロー**

```
1. active=True AND final_judge IS NULL の archive を探す
2. 見つかった → その archive に lanes, final_judge を書き足す
3. 見つからない（単体実行など） → 従来通り archive を新規作成して書き込む
```

### 5.3 pipeline_orchestrator

| 項目 | 現在 | 変更後 |
|------|------|--------|
| Phase 1 | Monitor | Technical |
| Phase 2 | Discussion | Monitor |
| Phase 3 | Planning | Discussion |
| Phase 4 | Watch | Planning |
| Phase 5 | — | Watch |
| propagate_active_after_discussion | 実行する | 削除 |

### Discussion 以降の開始条件（変更なし）

| ブロック | 開始条件 |
|---------|---------|
| Discussion | `active = True AND final_judge IS NULL` |
| Planning | `active = True AND final_judge IS NOT NULL AND newplan_full IS NULL` |
| Watch | `active = True AND status = 'completed' AND newplan_full IS NOT NULL` |

OK の archive は `active=False` のため下流に進まない。NG のみ Discussion 以降へ流れる。

---

## 6. DB 変更

### archive テーブル：新規カラム追加

| カラム | 型 | デフォルト | 説明 |
|--------|-----|-----------|------|
| `technical` | jsonb | NULL | テクニカル指標データ |

### 新規テーブル

なし。

### 同一 archive 方式の伝言板

全ブロックが1つの archive レコードに順番に書き足す。

| ブロック | 書き込み先カラム |
|---------|-----------------|
| Technical | `technical` |
| Monitor | `monitor`, `MotivationID`, `motivation_full`, `active`, `status` |
| Discussion | `lanes`, `final_judge` |
| Planning | `newplan_full`, `verdict`, `status` |
| Watch | watchlist テーブル更新 + `active`=False |

各ブロックは**自分の担当カラムのみ書き込み、他ブロックが書いたカラムは読み取り専用**。

---

## 7. 設定

### Technical/config.yaml

Technical ブロックの動作設定。投資方針ではなくシステムの動作パラメータなので、ブロック内に配置する。

```yaml
timeframes:
  - "1d"                # 日足（1日単位の株価で分析）

period: "1y"            # 過去1年分のデータを取得

indicator_profile: "core"       # core：基本22種 / extended：拡張30種
pattern_profile: "major_only"   # major_only：主要13種 / full：全20種
```

---

## 8. エラーハンドリング

| エラー種別 | 原因 | 対処 |
|-----------|------|------|
| yfinance データ取得失敗 | ネットワーク障害、銘柄コード不正 | リトライ（最大3回）→ 失敗時は `{"error": "..."}` を記録 + **Discord 通知** |
| 計算エラー | データ不足（バー数不足など） | warnings 付きで部分結果を記録 + **Discord 通知** |
| DB 書き込み失敗 | Supabase 障害 | `safe_db` ラッパーで吸収。ログ出力のみ |

Technical のエラーはパイプライン全体を止めない。
テクニカルデータがなくても Monitor・Discussion は従来通り動作可能（テクニカルデータは補助材料）。

Discord 通知は既存の `shared/discord_notifier.py` + `NotifyLabel.ERROR` を使用する。

---

## 9. 実装ステップ（案）

| # | 内容 | 備考 |
|---|------|------|
| 1 | `Technical/` ディレクトリ作成、venv 構築、TechnicalIndicatorFetcher インストール | uv でパッケージ管理 |
| 2 | DB migration：`archive.technical` カラム追加 | jsonb, nullable |
| 3 | `technical_orchestrator.py` 実装 | 全銘柄一括 / 単一銘柄の2モード |
| 4 | `shared/supabase_client.py` に `ensure_technical_data()` 追加 | ガード関数 |
| 5 | `pipeline_orchestrator.py` に Phase 1（Technical）を追加 | Monitor の前に実行 |
| 6 | Monitor の仕様変更 | archive 作成を削除、Technical が作った既存レコードに書き込む形に変更 |
| 7 | Discussion の仕様変更 | 既存 archive があればそこに書き足す。なければ従来通り作成 |
| 8 | `propagate_active_after_discussion()` の呼び出しを削除 | pipeline_orchestrator から除去 |
| 9 | Discussion 側に `ensure_technical_data()` 呼び出しを追加 | 議論開始前にガード |
| 10 | テスト | 単体テスト + パイプライン結合テスト |

---

## 10. スコープ外（将来の拡張候補）

- 下流ブロック（Monitor / Discussion / Planning）のプロンプトにテクニカルデータを組み込む具体的な実装
- 複数時間足の戦略的な使い分け（日足 + 週足のクロス分析など）
- テクニカルデータの履歴管理（現在は最新のスナップショットのみ保存）
- バックテスト連携
