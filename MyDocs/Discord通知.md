# Discord 通知ラベル一覧

パイプラインが Discord に送信する通知の種類と送信条件。

## ラベル一覧

| ラベル | 色 | 送信元 | 送信タイミング |
|--------|-----|--------|----------------|
| **開始** | 水色 | pipeline_orchestrator | パイプライン開始時 |
| **緊急** | 赤 | Watch | Monitor 結果が NG かつ変動率 ≤ −10% |
| **朗報** | 緑 | Watch | Monitor 結果が NG かつ変動率 ≥ +10% |
| **警告** | オレンジ | Watch | Monitor 結果が NG（変動率 −10%〜+10% の範囲） |
| **確認** | 青 | pipeline_orchestrator | Monitor 結果が OK だがリスクフラグあり |
| **完了** | 緑 | pipeline_orchestrator | 全銘柄チェック完了時 |
| **エラー** | グレー | pipeline_orchestrator | リトライ上限到達 / 失敗 |

> Monitor 結果が OK でリスクフラグなし → 通知なし

## 送信元の役割分担

- **pipeline_orchestrator**：パイプライン制御系の通知（開始・完了・エラー・確認）を担当
- **Watch**：業務判定系の通知（緊急・朗報・警告）を担当。Discussion → Planning 完了後、`archive.monitor` のデータをもとに `classify_label()` でラベルを判定して送信する

## 補足

- **完了**通知のメッセージ内容は状況によって変わる。NG 銘柄なしの場合は全銘柄 OK を示し、NG 銘柄ありの場合は全銘柄チェック完了とその内訳を示す
- **緊急・朗報・警告**の判定はすべて Monitor 結果が NG の場合のみ適用される。変動率の閾値（±10%）は `shared/notification_types.py` の `classify_label()` で管理している
