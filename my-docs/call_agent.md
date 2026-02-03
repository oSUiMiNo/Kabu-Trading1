# 関数 call_agent()

## どんな機能か

-> `プロンプト` と `ファイル` を渡すと、ファイルは `オプション（縛り）` に変換し、
-> Claude Code に `プロンプト` と `オプション` を送信し、
-> 応答を表示する


## 使用例

### プロンプトのみ（file_path なし）

```python
await call_agent("こんちわー")
```

### エージェント定義ファイルを指定

```python
await call_agent("〇〇銘柄を分析して", file_path=".claude/agents/analyst.md")
```
- `.claude/agents/analyst.md` には株式アナリストとして振舞ってもらうための縛りが記載されている（一番下参照）


## 引数

- **messages** → プロンプト（必須）
- **file_path** → エージェント定義ファイル（オプション）
- **show_cost** → コスト表示（デフォルト: True）
- **show_tools** → ツール表示（デフォルト: False）

```python
async def call_agent(
    messages,                              # プロンプト
    file_path: str | Path | None = None,   # オプション
    show_cost: bool = True,
    show_tools: bool = False,
)
```

### file_path の使い分け

| 用途 | file_path | 動作 |
|------|-----------|------|
| 手軽に質問 | 省略 | プロンプトをそのまま送信 |
| エージェントとして動作 | 指定 | ファイルから system_prompt / tools / model を適用 |


## 処理フロー

```
call_agent(messages, file_path)
        │
        ├─ file_path = None
        │       └─ options = None
        │
        └─ file_path 指定あり
                └─ parse_agent_file(file_path)
                        ├─ YAML部分 → allowed_tools, model 等
                        └─ Markdown本文 → system_prompt
        │
        └─ query(prompt=messages, options=options) → 応答表示
```
- `parse_agent_file()`：ファイルの内容をオプションとして**パース**する関数
- `query()`：AIに話しかける関数


## エージェント定義ファイルの形式

`file_path` に指定するファイルは以下の形式に対応

### フロントマター付き

```markdown
---
name: analyst
description: 株銘柄の考察・分析を行う
tools:
  - Read
  - Write
  - WebSearch
model: sonnet
---

# Analyst

あなたは株式アナリストです。
```
- YAML部分 → `allowed_tools`, `model` に割り当てられる
- Markdown本文 → `system_prompt` に割り当てられる

### フロントマター無し（.md, .txt 等のテキストファイル）

```markdown
# Analyst

あなたは株式アナリストです。
```
- ファイル内容全体 → `system_prompt` に設定
