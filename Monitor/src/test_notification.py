"""
通知機能の単体テスト

対象:
  - classify_label: ラベル判定ロジック
  - build_embed: Discord Embed 構築
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from notification_types import NotifyLabel, NotifyPayload, classify_label, LABEL_COLOR
from discord_notifier import build_embed


class TestClassifyLabel(unittest.TestCase):
    """classify_label の全パターンテスト"""

    def test_ng_large_drop_returns_urgent(self):
        data = {"result": "NG", "price_change_pct": -15.0}
        self.assertEqual(classify_label(data), NotifyLabel.URGENT)

    def test_ng_exactly_minus_10_returns_urgent(self):
        data = {"result": "NG", "price_change_pct": -10.0}
        self.assertEqual(classify_label(data), NotifyLabel.URGENT)

    def test_ng_large_rise_returns_good_news(self):
        data = {"result": "NG", "price_change_pct": 12.5}
        self.assertEqual(classify_label(data), NotifyLabel.GOOD_NEWS)

    def test_ng_exactly_plus_10_returns_good_news(self):
        data = {"result": "NG", "price_change_pct": 10.0}
        self.assertEqual(classify_label(data), NotifyLabel.GOOD_NEWS)

    def test_ng_moderate_change_returns_warning(self):
        data = {"result": "NG", "price_change_pct": -5.0}
        self.assertEqual(classify_label(data), NotifyLabel.WARNING)

    def test_ng_no_pct_returns_warning(self):
        data = {"result": "NG"}
        self.assertEqual(classify_label(data), NotifyLabel.WARNING)

    def test_ng_zero_pct_returns_warning(self):
        data = {"result": "NG", "price_change_pct": 0.0}
        self.assertEqual(classify_label(data), NotifyLabel.WARNING)

    def test_ok_with_risk_flags_returns_check(self):
        data = {"result": "OK", "risk_flags": ["price_deviation_exceeded"]}
        self.assertEqual(classify_label(data), NotifyLabel.CHECK)

    def test_ok_no_flags_returns_none(self):
        data = {"result": "OK", "risk_flags": []}
        self.assertIsNone(classify_label(data))

    def test_ok_no_flags_key_returns_none(self):
        data = {"result": "OK"}
        self.assertIsNone(classify_label(data))

    def test_retries_exhausted_returns_error(self):
        data = {"result": "ERROR", "retries_exhausted": True}
        self.assertEqual(classify_label(data), NotifyLabel.ERROR)

    def test_retries_exhausted_overrides_result(self):
        data = {"result": "NG", "retries_exhausted": True, "price_change_pct": -20.0}
        self.assertEqual(classify_label(data), NotifyLabel.ERROR)


class TestBuildEmbed(unittest.TestCase):
    """build_embed の Embed 構造テスト"""

    def test_warning_embed_structure(self):
        payload = NotifyPayload(
            label=NotifyLabel.WARNING,
            ticker="NVDA",
            monitor_data={
                "result": "NG",
                "current_price": 115.0,
                "plan_price": 135.0,
                "price_change_pct": -14.81,
                "summary": "大幅下落",
                "ng_reason": "価格乖離",
                "risk_flags": ["price_deviation_exceeded"],
            },
        )
        embed = build_embed(payload)
        self.assertEqual(embed["title"], "【警告】NVDA")
        self.assertEqual(embed["color"], LABEL_COLOR[NotifyLabel.WARNING])
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("結果", field_names)
        self.assertIn("現在価格", field_names)
        self.assertIn("NG理由", field_names)

    def test_urgent_with_new_plan(self):
        payload = NotifyPayload(
            label=NotifyLabel.URGENT,
            ticker="TSLA",
            monitor_data={
                "result": "NG",
                "current_price": 200.0,
                "plan_price": 250.0,
                "price_change_pct": -20.0,
                "summary": "急落",
                "ng_reason": "急落",
            },
            new_plan={
                "decision_final": "NOT_BUY_WAIT",
                "confidence": 0.75,
                "allocation_jpy": 50000,
                "quantity": 2,
            },
        )
        embed = build_embed(payload)
        self.assertEqual(embed["title"], "【緊急】TSLA")
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("── 新プラン ──", field_names)
        self.assertIn("判定", field_names)
        self.assertIn("配分額", field_names)

    def test_check_embed(self):
        payload = NotifyPayload(
            label=NotifyLabel.CHECK,
            ticker="AAPL",
            monitor_data={
                "result": "OK",
                "current_price": 180.0,
                "plan_price": 175.0,
                "price_change_pct": 2.86,
                "summary": "概ね問題なし",
                "risk_flags": ["macro_shock"],
            },
        )
        embed = build_embed(payload)
        self.assertEqual(embed["title"], "【確認】AAPL")
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("リスクフラグ", field_names)
        self.assertNotIn("── 新プラン ──", field_names)

    def test_error_embed(self):
        payload = NotifyPayload(
            label=NotifyLabel.ERROR,
            ticker="GOOG",
            monitor_data={"result": "ERROR", "retries_exhausted": True},
            error_detail="Monitor リトライ上限到達",
        )
        embed = build_embed(payload)
        self.assertEqual(embed["title"], "【エラー】GOOG")
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("エラー詳細", field_names)
        self.assertEqual(len(embed["fields"]), 1)

    def test_beginner_summary_in_description(self):
        payload = NotifyPayload(
            label=NotifyLabel.WARNING,
            ticker="NVDA",
            monitor_data={
                "result": "NG",
                "current_price": 115.0,
                "plan_price": 135.0,
                "price_change_pct": -14.81,
                "summary": "下落",
            },
            beginner_summary="NVDAの株価が下がりました。",
        )
        embed = build_embed(payload)
        self.assertEqual(embed["description"], "NVDAの株価が下がりました。")

    def test_event_context_field(self):
        payload = NotifyPayload(
            label=NotifyLabel.WARNING,
            ticker="NVDA",
            monitor_data={
                "result": "NG",
                "current_price": 100.0,
                "plan_price": 110.0,
                "price_change_pct": -9.09,
                "summary": "下落",
            },
            event_context={
                "event_id": "fomc_rate",
                "name_ja": "FOMC金利決定",
                "watch_kind": "post_release_5m",
            },
        )
        embed = build_embed(payload)
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("トリガイベント", field_names)
        event_field = next(f for f in embed["fields"] if f["name"] == "トリガイベント")
        self.assertIn("FOMC金利決定", event_field["value"])
        self.assertIn("発表5分後", event_field["value"])


class TestCompleteEmbed(unittest.TestCase):
    """【完了】通知の Embed テスト"""

    def test_complete_us_embed(self):
        payload = NotifyPayload(
            label=NotifyLabel.COMPLETE,
            ticker="米国株 全銘柄OK",
            monitor_data={"tickers": ["NVDA", "AAPL", "TSLA"]},
        )
        embed = build_embed(payload)
        self.assertEqual(embed["title"], "【完了】米国株 全銘柄OK")
        self.assertEqual(embed["color"], LABEL_COLOR[NotifyLabel.COMPLETE])
        self.assertIn("全銘柄のプランが現在の市場状況に対して有効です。", embed["description"])
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("チェック数", field_names)
        self.assertIn("銘柄", field_names)
        count_field = next(f for f in embed["fields"] if f["name"] == "チェック数")
        self.assertEqual(count_field["value"], "3 銘柄")
        tickers_field = next(f for f in embed["fields"] if f["name"] == "銘柄")
        self.assertEqual(tickers_field["value"], "NVDA, AAPL, TSLA")

    def test_complete_jp_embed(self):
        payload = NotifyPayload(
            label=NotifyLabel.COMPLETE,
            ticker="日本株 全銘柄OK",
            monitor_data={"tickers": ["7203", "9984"]},
        )
        embed = build_embed(payload)
        self.assertEqual(embed["title"], "【完了】日本株 全銘柄OK")
        count_field = next(f for f in embed["fields"] if f["name"] == "チェック数")
        self.assertEqual(count_field["value"], "2 銘柄")

    def test_complete_no_new_plan_fields(self):
        payload = NotifyPayload(
            label=NotifyLabel.COMPLETE,
            ticker="全銘柄 全銘柄OK",
            monitor_data={"tickers": ["NVDA"]},
        )
        embed = build_embed(payload)
        field_names = [f["name"] for f in embed["fields"]]
        self.assertNotIn("── 新プラン ──", field_names)
        self.assertNotIn("結果", field_names)


class TestJapaneseTranslation(unittest.TestCase):
    """英語値が日本語に翻訳されているかのテスト"""

    def test_risk_flags_translated(self):
        payload = NotifyPayload(
            label=NotifyLabel.WARNING,
            ticker="NVDA",
            monitor_data={
                "result": "NG",
                "current_price": 100.0,
                "plan_price": 110.0,
                "price_change_pct": -9.0,
                "summary": "下落",
                "risk_flags": ["price_deviation_exceeded", "earnings_miss"],
            },
        )
        embed = build_embed(payload)
        flag_field = next(f for f in embed["fields"] if f["name"] == "リスクフラグ")
        self.assertIn("価格乖離超過", flag_field["value"])
        self.assertIn("決算未達", flag_field["value"])
        self.assertNotIn("price_deviation_exceeded", flag_field["value"])

    def test_decision_final_translated(self):
        payload = NotifyPayload(
            label=NotifyLabel.WARNING,
            ticker="TSLA",
            monitor_data={"result": "NG", "current_price": 200.0, "plan_price": 210.0,
                          "price_change_pct": -4.76, "summary": "下落"},
            new_plan={"decision_final": "BUY", "confidence": 0.8},
        )
        embed = build_embed(payload)
        decision_field = next(f for f in embed["fields"] if f["name"] == "判定")
        self.assertEqual(decision_field["value"], "買い")

    def test_confidence_field_name_japanese(self):
        payload = NotifyPayload(
            label=NotifyLabel.WARNING,
            ticker="TSLA",
            monitor_data={"result": "NG", "current_price": 200.0, "plan_price": 210.0,
                          "price_change_pct": -4.76, "summary": "下落"},
            new_plan={"decision_final": "SELL", "confidence": 0.9},
        )
        embed = build_embed(payload)
        field_names = [f["name"] for f in embed["fields"]]
        self.assertIn("確信度", field_names)
        self.assertNotIn("confidence", field_names)

    def test_result_error_translated(self):
        payload = NotifyPayload(
            label=NotifyLabel.WARNING,
            ticker="AAPL",
            monitor_data={"result": "ERROR", "current_price": None, "plan_price": 180.0,
                          "price_change_pct": None, "summary": "失敗"},
        )
        embed = build_embed(payload)
        result_field = next(f for f in embed["fields"] if f["name"] == "結果")
        self.assertEqual(result_field["value"], "エラー")

    def test_watch_kind_translated(self):
        payload = NotifyPayload(
            label=NotifyLabel.WARNING,
            ticker="NVDA",
            monitor_data={"result": "NG", "current_price": 100.0, "plan_price": 110.0,
                          "price_change_pct": -9.0, "summary": "下落"},
            event_context={"event_id": "cpi", "name_ja": "消費者物価指数", "watch_kind": "post_release_20m"},
        )
        embed = build_embed(payload)
        event_field = next(f for f in embed["fields"] if f["name"] == "トリガイベント")
        self.assertIn("発表20分後", event_field["value"])
        self.assertNotIn("post_release_20m", event_field["value"])


if __name__ == "__main__":
    unittest.main()
