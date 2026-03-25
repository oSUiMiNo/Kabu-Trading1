"""
ActionLog — 銘柄ごとの月次アクションログ表示 Web UI

起動: cd ActionLog/src && uv run python main.py
ブラウザ: http://localhost:8080
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nicegui import ui

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import safe_db, get_action_log_handoff

from data_service import (
    get_monthly_data,
    handle_edit,
    get_ticker_list,
    get_available_months,
    populate_existing_archives,
)
from handoff_service import get_cached_handoff, get_or_generate_handoff

_JST = timezone(timedelta(hours=9))

# ── JS ヘルパー（通貨フォーマット・損益色分け） ──────────────

ui.add_head_html(shared=True, code="""
<script>
function formatJPY(params) {
    if (params.value == null || params.value === '') return '';
    return new Intl.NumberFormat('ja-JP').format(params.value);
}
function pnlCellStyle(params) {
    if (params.value == null || params.value === '') return {};
    if (params.value > 0) return {color: '#16a34a'};
    if (params.value < 0) return {color: '#dc2626'};
    return {};
}
function handoffEditable(params) {
    return !params.data.is_handoff;
}
</script>
""")


# ── 表示データ構築 ───────────────────────────────────────

def _build_display_rows(
    rows: list[dict], handoff_text: str | None
) -> list[dict]:
    """DB の行データに引き継ぎ文行を先頭に挿入して表示用リストを作る。"""
    display = []
    if handoff_text:
        display.append({
            "id": None,
            "is_handoff": True,
            "action_date": "",
            "action_text": "",
            "story": f"📝 引き継ぎ：{handoff_text}",
            "cumulative_invested": None,
            "total_assets": None,
            "pnl": None,
        })
    for r in rows:
        row = dict(r)
        row["is_handoff"] = False
        date_val = row.get("action_date", "")
        if date_val and len(str(date_val)) >= 10:
            d = str(date_val)[:10]
            row["action_date"] = f"{d[5:7]}/{d[8:10]}"
        display.append(row)
    return display


# ── AG Grid カラム定義 ────────────────────────────────────

_COLUMN_DEFS = [
    {
        "headerName": "TS",
        "field": "action_date",
        "width": 80,
        "editable": False,
        "sortable": False,
    },
    {
        "headerName": "アクションログ",
        "field": "action_text",
        ":editable": "handoffEditable",
        "autoHeight": True,
        "wrapText": True,
        "width": 150,
    },
    {
        "headerName": "ストーリー",
        "field": "story",
        ":editable": "handoffEditable",
        "autoHeight": True,
        "wrapText": True,
        "width": 400,
    },
    {
        "headerName": "入れたお金",
        "field": "cumulative_invested",
        ":editable": "handoffEditable",
        ":valueFormatter": "formatJPY",
        "width": 110,
        "type": "numericColumn",
    },
    {
        "headerName": "総資産",
        "field": "total_assets",
        ":editable": "handoffEditable",
        ":valueFormatter": "formatJPY",
        "width": 110,
        "type": "numericColumn",
    },
    {
        "headerName": "損益",
        "field": "pnl",
        "editable": False,
        ":valueFormatter": "formatJPY",
        ":cellStyle": "pnlCellStyle",
        "width": 100,
        "type": "numericColumn",
    },
]


# ── ルートページ（銘柄選択） ──────────────────────────────

@ui.page("/")
def index_page():
    ui.label("ActionLog").classes("text-2xl font-bold mb-4")

    tickers = get_ticker_list()

    with ui.card().classes("w-96"):
        ui.label("銘柄を選択してください").classes("text-lg mb-2")

        if tickers:
            select = ui.select(
                options=tickers,
                label="銘柄",
                with_input=True,
            ).classes("w-full")

            def go_to_ticker():
                if select.value:
                    ui.navigate.to(f"/ticker/{select.value}")

            ui.button("表示", on_click=go_to_ticker).classes("mt-2")
        else:
            ui.label("銘柄がありません。先に archive を取り込んでください。").classes("text-gray-500")

        ui.separator().classes("my-4")

        ui.label("既存 archive の取り込み").classes("text-sm font-bold")

        import_ticker = ui.input(label="銘柄コード（例: NVDA）").classes("w-full")
        import_result = ui.label("").classes("text-sm mt-1")

        def do_import():
            t = (import_ticker.value or "").strip().upper()
            if not t:
                import_result.set_text("銘柄コードを入力してください")
                return
            count = populate_existing_archives(t)
            import_result.set_text(f"{t}: {count} 件取り込みました")
            ui.navigate.to(f"/ticker/{t}")

        ui.button("取り込み", on_click=do_import).classes("mt-2")


# ── アクションログページ ──────────────────────────────────

@ui.page("/ticker/{ticker}")
def ticker_page(ticker: str):
    ticker = ticker.upper()
    now = datetime.now(_JST)
    state = {"year": now.year, "month": now.month}

    ui.link("← 銘柄選択に戻る", "/").classes("text-sm mb-2")

    # ── 銘柄ナビ ──
    tickers = get_ticker_list()
    with ui.row().classes("items-center gap-2 my-1"):
        def prev_ticker():
            if ticker in tickers:
                idx = tickers.index(ticker)
                new_ticker = tickers[(idx - 1) % len(tickers)]
            else:
                new_ticker = tickers[0] if tickers else ticker
            ui.navigate.to(f"/ticker/{new_ticker}")

        def next_ticker():
            if ticker in tickers:
                idx = tickers.index(ticker)
                new_ticker = tickers[(idx + 1) % len(tickers)]
            else:
                new_ticker = tickers[0] if tickers else ticker
            ui.navigate.to(f"/ticker/{new_ticker}")

        ui.button("←", on_click=prev_ticker).props("flat dense")
        ui.label(f"{ticker}").classes("text-2xl font-bold min-w-[100px] text-center")
        ui.button("→", on_click=next_ticker).props("flat dense")

    # ── 月ナビ ──
    with ui.row().classes("items-center gap-2 my-2"):
        def prev_month():
            if state["month"] == 1:
                state["year"] -= 1
                state["month"] = 12
            else:
                state["month"] -= 1
            refresh_grid()

        def next_month():
            if state["month"] == 12:
                state["year"] += 1
                state["month"] = 1
            else:
                state["month"] += 1
            refresh_grid()

        ui.button("←", on_click=prev_month).props("flat dense")
        month_label = ui.label(f"{state['year']}年{state['month']}月").classes(
            "text-lg font-bold min-w-[120px] text-center"
        )
        ui.button("→", on_click=next_month).props("flat dense")

    # ── AG Grid ──
    grid = ui.aggrid(
        {
            "columnDefs": _COLUMN_DEFS,
            "rowData": [],
            "stopEditingWhenCellsLoseFocus": True,
        },
        theme="balham",
    ).classes("w-full").style("height: calc(100vh - 180px)")

    # ── データ読み込み・表示更新 ──
    def refresh_grid():
        y, m = state["year"], state["month"]
        month_label.set_text(f"{y}年{m}月")

        rows = get_monthly_data(ticker, y, m)

        # まずキャッシュ済みの引き継ぎ文を同期で取得（即座に表示）
        handoff_text = get_cached_handoff(ticker, y, m)

        display = _build_display_rows(rows, handoff_text)
        grid.options["rowData"] = display
        grid.update()

        # キャッシュがなく前月データがある場合、バックグラウンドで生成
        if handoff_text is None:
            async def _generate_and_update():
                text = await get_or_generate_handoff(ticker, y, m)
                if text and state["year"] == y and state["month"] == m:
                    updated_display = _build_display_rows(rows, text)
                    grid.options["rowData"] = updated_display
                    grid.update()

            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_generate_and_update())
                else:
                    loop.run_until_complete(_generate_and_update())
            except RuntimeError:
                pass

    # ── セル編集イベント ──
    def on_cell_changed(e):
        row_data = e.args.get("data", {})
        if row_data.get("is_handoff"):
            return

        col_def = e.args.get("colDef", {})
        field = col_def.get("field")
        if not field:
            return

        log_id = row_data.get("id")
        if not log_id:
            return

        new_value = row_data.get(field)
        handle_edit(log_id, ticker, field, new_value)
        refresh_grid()

    grid.on("cellValueChanged", on_cell_changed)

    # 初回表示
    refresh_grid()


# ── 起動 ──────────────────────────────────────────────

ui.run(port=8080, title="ActionLog", reload=False)
