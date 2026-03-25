## ActionLog ブロック

銘柄ごとの月次アクションログを表示するローカル Web アプリ。

### 起動方法

```bash
cd ActionLog/src
uv run python main.py
```

ブラウザで http://localhost:8080 にアクセス。

### ファイル構成

| ファイル | 役割 |
|----------|------|
| `src/main.py` | NiceGUI アプリ本体 |
| `src/data_service.py` | DB の読み書き |
| `src/auto_populate.py` | archive → action_log への変換 |
| `src/calc_engine.py` | 自動計算（編集時の連鎖再計算） |
| `src/handoff_service.py` | 引き継ぎ文の生成・保存 |
| `.claude/commands/handoff-generator.md` | 引き継ぎ文生成サブエージェント |
