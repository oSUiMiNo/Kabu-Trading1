# ブロックオーケストレーター リネーム計画

各ブロックのオーケストレーターを `main.py` に統一する。

## 対象ファイル

| ブロック | Before | After |
|---|---|---|
| Technical | `Technical/src/technical_orchestrator.py` | `Technical/src/main.py` |
| Monitor | `Monitor/src/monitor_orchestrator.py` | `Monitor/src/main.py` |
| Discussion | `Discusion/src/parallel_orchestrator.py` | `Discusion/src/main.py` |
| Planning | `Planning/src/plan_orchestrator.py` | `Planning/src/main.py` |
| Watch | `Watch/src/watch_orchestrator.py` | `Watch/src/main.py` |
| NightWorker | `NightWorker/src/review_orchestrator.py` | `NightWorker/src/main.py` |
| EventScheduler | `EventScheduler/src/scheduler_orchestrator.py` | `EventScheduler/src/main.py` |

---

## Step 1：ファイルリネーム（全7ファイル）

旧ファイルを新名に変更する。全ブロック一括で行う。

| 操作 | 対象 |
|---|---|
| rename | `Technical/src/technical_orchestrator.py` → `Technical/src/main.py` |
| rename | `Monitor/src/monitor_orchestrator.py` → `Monitor/src/main.py` |
| rename | `Discusion/src/parallel_orchestrator.py` → `Discusion/src/main.py` |
| rename | `Planning/src/plan_orchestrator.py` → `Planning/src/main.py` |
| rename | `Watch/src/watch_orchestrator.py` → `Watch/src/main.py` |
| rename | `NightWorker/src/review_orchestrator.py` → `NightWorker/src/main.py` |
| rename | `EventScheduler/src/scheduler_orchestrator.py` → `EventScheduler/src/main.py` |

---

## Step 2：main_pipeline.py のコード修正

プロジェクト直下の `main_pipeline.py` が各ブロックを subprocess やimport で呼んでいる箇所を修正する。

| 行 | Before | After | 備考 |
|---|---|---|---|
| 37 | `from monitor_orchestrator import run_monitor` | `from main import run_monitor` | import 文 |
| 83 | `TECHNICAL_DIR / "src" / "technical_orchestrator.py"` | `TECHNICAL_DIR / "src" / "main.py"` | subprocess |
| 104 | `DISCUSSION_DIR / "parallel_orchestrator.py"` | `DISCUSSION_DIR / "main.py"` | subprocess |
| 123 | `PLANNING_DIR / "plan_orchestrator.py"` | `PLANNING_DIR / "main.py"` | subprocess |
| 142 | `WATCH_DIR / "watch_orchestrator.py"` | `WATCH_DIR / "main.py"` | subprocess |

---

## Step 3：shared のコード修正

`shared/supabase_client.py` が Technical を直接呼ぶ箇所を修正する。

| 行 | Before | After |
|---|---|---|
| 266 | `technical_dir / "src" / "technical_orchestrator.py"` | `technical_dir / "src" / "main.py"` |

---

## Step 4：ブロック内部のコード修正

各ブロック内で旧ファイル名を参照しているコード（import、コメント、print文、docstring）を修正する。

### Technical

| ファイル | 行 | 内容 |
|---|---|---|
| `Technical/src/main.py` | 8-9 | docstring 内の `python technical_orchestrator.py` → `python main.py` |

### Monitor

| ファイル | 行 | 内容 |
|---|---|---|
| `Monitor/src/main.py` | 8-12 | docstring 内の `python monitor_orchestrator.py` → `python main.py` |

### Discussion

| ファイル | 行 | 内容 |
|---|---|---|
| `Discusion/src/main.py` | 304-305 | print文内の `python parallel_orchestrator.py` → `python main.py` |
| `Discusion/src/lane_orchestrator.py` | 151 | コメント `parallel_orchestrator` → `main` |

### Planning

| ファイル | 行 | 内容 |
|---|---|---|
| `Planning/src/main.py` | 11-20 | docstring 内の `python plan_orchestrator.py` → `python main.py` |
| `Planning/src/main.py` | 304 | 関数名 `run_plan_orchestrator` → 任意（要検討） |
| `Planning/src/main.py` | 569, 640 | 関数呼び出し `run_plan_orchestrator` → 上記に合わせる |
| `Planning/src/main.py` | 592-593 | print文内の `python plan_orchestrator.py` → `python main.py` |

### Watch

| ファイル | 行 | 内容 |
|---|---|---|
| `Watch/src/main.py` | 8-9 | docstring 内の `python watch_orchestrator.py` → `python main.py` |

### NightWorker

| ファイル | 行 | 内容 |
|---|---|---|
| `NightWorker/src/main.py` | 8-10 | docstring 内の `python review_orchestrator.py` → `python main.py` |

### EventScheduler

| ファイル | 行 | 内容 |
|---|---|---|
| `EventScheduler/src/main.py` | 7-10, 308 | docstring・print文内の `python scheduler_orchestrator.py` → `python main.py` |
| `EventScheduler/src/event_master_seed.py` | 4 | コメント `scheduler_orchestrator.py seed` → `main.py seed` |

---

## Step 5：ワークフロー修正（GitHub Actions）

`.github/workflows/` 内でスクリプト名を直接指定している箇所を修正する。

| ファイル | 行 | Before | After |
|---|---|---|---|
| `night-worker.yml` | 63 | `uv run python review_orchestrator.py $ARGS` | `uv run python main.py $ARGS` |
| `event-scheduler.yml` | 94 | `uv run python scheduler_orchestrator.py seed` | `uv run python main.py seed` |
| `event-scheduler.yml` | 96 | `uv run python scheduler_orchestrator.py annual` | `uv run python main.py annual` |
| `event-scheduler.yml` | 98 | `uv run python scheduler_orchestrator.py monthly --months "$MONTHS"` | `uv run python main.py monthly --months "$MONTHS"` |

> `monitor.yml` / `event-monitor.yml` は Step 2 で修正済みの `main_pipeline.py` を呼ぶだけなので変更不要。

---

## Step 6：CLAUDE.md 修正（プロジェクト設定）

各ブロックの `.claude/CLAUDE.md` 内のファイル名参照を修正する。

| ファイル | 行 | 内容 |
|---|---|---|
| `Monitor/.claude/CLAUDE.md` | 10 | `src/monitor_orchestrator.py` → `src/main.py` |
| `Monitor/.claude/CLAUDE.md` | 21 | 実行コマンドの修正 |
| `Monitor/.claude/CLAUDE.md` | 30 | 重要ファイル表の修正 |
| `Planning/.claude/CLAUDE.md` | 17 | `src/plan_orchestrator.py` → `src/main.py` |
| `Planning/.claude/CLAUDE.md` | 25 | 重要ファイル表の修正 |
| `NightWorker/.claude/CLAUDE.md` | 14 | `src/review_orchestrator.py` → `src/main.py` |

---

## Step 7：ドキュメント修正（MyDocs・README）

### README.md

| 行 | 内容 |
|---|---|
| 34 | `Technical/src/technical_orchestrator.py` → `Technical/src/main.py` |
| 35 | `Monitor/src/monitor_orchestrator.py` → `Monitor/src/main.py` |
| 36 | `Discusion/src/parallel_orchestrator.py` → `Discusion/src/main.py` |
| 37 | `Planning/src/plan_orchestrator.py` → `Planning/src/main.py` |
| 38 | `Watch/src/watch_orchestrator.py` → `Watch/src/main.py` |
| 39 | `EventScheduler/src/scheduler_orchestrator.py` → `EventScheduler/src/main.py` |
| 40 | `NightWorker/src/review_orchestrator.py` → `NightWorker/src/main.py` |
| 61 | `python Monitor/src/monitor_orchestrator.py` → `python Monitor/src/main.py` |

### MyDocs/新アーキテクチャ設計.md

| 行 | 内容 |
|---|---|
| 128 | `technical_orchestrator.py` → `main.py` |
| 133 | `monitor_orchestrator.py::run_monitor()` → `main.py::run_monitor()` |

### MyDocs/Technical要件定義書.md

| 行 | 内容 |
|---|---|
| 106 | ディレクトリ構成の `technical_orchestrator.py` → `main.py` |
| 132-133 | Usage表の `python technical_orchestrator.py` → `python main.py` |
| 377 | 実装ステップ表の `technical_orchestrator.py` → `main.py` |

### MyDocs/過去のシステムアーキテクチャ.md

| 行 | 内容 |
|---|---|
| 38 | `monitor_orchestrator.py` → `main.py` |
| 70 | `parallel_orchestrator.py` → `main.py` |
| 76 | `plan_orchestrator.py` → `main.py` |
| 133-136 | Monitor 関数一覧表の `monitor_orchestrator.py` → `main.py` |
| 165 | `parallel_orchestrator.py` → `main.py` |
| 178 | `plan_orchestrator.py` → `main.py` |
| 214 | `plan_orchestrator.py` → `main.py` |

### MyDocs/証拠ベース議論改修計画.md

| 行 | 内容 |
|---|---|
| 116 | `Planning/src/plan_orchestrator.py` → `Planning/src/main.py` |
| 139 | `Monitor/src/monitor_orchestrator.py` → `Monitor/src/main.py` |

### Discusion/MyDocs/project-summary.md

| 行 | 内容 |
|---|---|
| 23 | フローチャートの `parallel_orchestrator.py` → `main.py` |
| 108 | 実行コマンドの `parallel_orchestrator.py` → `main.py` |
| 111-112 | Usage 具体例の `parallel_orchestrator.py` → `main.py` |
| 124 | ディレクトリ構成の `parallel_orchestrator.py` → `main.py` |

---

## Step 8：メモリ修正

| ファイル | 内容 |
|---|---|
| `~/.claude/projects/.../memory/MEMORY.md` | 重要ファイル表の `monitor_orchestrator.py` → `main.py` |
| `~/.claude/projects/.../memory/MEMORY.md` | 重要ファイル表の `plan_orchestrator.py` → `main.py` |

---

## Step 9：最終検証

1. 旧ファイル名で grep し、参照が残っていないことを確認
   - `technical_orchestrator`
   - `monitor_orchestrator`
   - `parallel_orchestrator`
   - `plan_orchestrator`
   - `watch_orchestrator`
   - `review_orchestrator`
   - `scheduler_orchestrator`
2. 各 `main.py` の構文チェック（`python -c "import py_compile; py_compile.compile('...')"`)
3. `main_pipeline.py` の import が通ることを確認
