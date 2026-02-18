"""
Discussionプロジェクト ログ解析モジュール

Discussion の final_judge ログから判定・投票・根拠を抽出する。
EXPORT yamlブロックを優先的にパースし、フォールバックとして本文マークダウンを解析する。
"""
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml


# Discussion → Planning の用語変換テーブル
DECISION_MAP: dict[str, str] = {
    "BUY": "BUY",
    "NOT_BUY_WAIT": "NO_BUY",
    "SELL": "SELL",
    "NOT_SELL_HOLD": "NO_SELL",
}


@dataclass
class DecisionBasis:
    """判定の根拠1件"""
    fact_id: str         # "F12" など（抽出できない場合は空文字）
    source_id: str       # "S3" など（抽出できない場合は空文字）
    text: str            # 根拠の全文テキスト


@dataclass
class ParsedJudgment:
    """final_judge ログから抽出したデータ"""
    ticker: str
    decision: str                       # "BUY" | "NO_BUY" | "SELL" | "NO_SELL"
    decision_raw: str                   # 変換前の値（"NOT_BUY_WAIT" 等）
    vote_for: int                       # アクション側の票数
    vote_against: int                   # 安全側の票数
    overall_agreement: str              # "AGREED_STRONG" | "MIXED" | "INCOMPLETE"
    log_date: datetime                  # ログ作成日時
    decision_basis: list[DecisionBasis] # 根拠リスト
    raw_text: str                       # ログ全文（エージェントに渡す用）


@dataclass
class SessionLogs:
    """指定銘柄のログファイル一式"""
    final_judge: Path | None = None
    set_files: list[Path] = field(default_factory=list)
    judge_files: list[Path] = field(default_factory=list)


def find_session_logs(session_dir: Path, ticker: str) -> SessionLogs:
    """
    セッションディレクトリから指定銘柄のログファイルを探索する。

    対象ファイル（7件）:
    - {TICKER}_set1.md, _set2.md, _set3.md  → set_files（議論ログ）
    - {TICKER}_set{N}_judge_{K}.md           → judge_files（判定）
    - {TICKER}_final_judge_{K}.md            → final_judge（最大番号を採用）
    """
    result = SessionLogs()
    t = ticker.upper()

    if not session_dir.exists():
        return result

    final_judges = sorted(session_dir.glob(f"{t}_final_judge_*.md"))
    if final_judges:
        result.final_judge = final_judges[-1]

    result.set_files = sorted(
        p for p in session_dir.glob(f"{t}_set*.md")
        if re.search(r"_set\d+\.md$", p.name)
    )

    result.judge_files = sorted(
        p for p in session_dir.glob(f"{t}_set*_judge_*.md")
    )

    return result


def _extract_export_yaml(text: str) -> dict | None:
    """
    ログ本文から EXPORT yamlブロック (```yaml ... ```) を抽出してパースする。
    複数ある場合は最後のブロックを使用する。
    """
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
    if not blocks:
        return None

    try:
        return yaml.safe_load(blocks[-1].strip()) or {}
    except yaml.YAMLError:
        return None


def _extract_decision_from_export(export: dict) -> str | None:
    """EXPORT yaml から判定結果を抽出"""
    # 最終判定.支持側
    final = export.get("最終判定", {})
    if isinstance(final, dict):
        side = final.get("支持側")
        if side:
            return str(side).strip("*").strip()

    # フラットキー
    for key in ("支持側", "supported_side"):
        if key in export:
            return str(export[key]).strip("*").strip()

    return None


def _extract_agreement_from_export(export: dict) -> str | None:
    """EXPORT yaml から総合一致度を抽出"""
    final = export.get("最終判定", {})
    if isinstance(final, dict):
        agree = final.get("総合一致度")
        if agree:
            return str(agree).strip("*").strip()

    for key in ("総合一致度", "overall_agreement"):
        if key in export:
            return str(export[key]).strip("*").strip()

    return None


def _extract_votes_from_export(export: dict) -> tuple[int, int] | None:
    """
    EXPORT yaml から投票数を抽出する。

    投票集計セクションの形式:
    投票集計（オーケストレーター算出・確定値）:
      BUY票: 1
      NOT_BUY_WAIT票: 3
    """
    # 投票集計キーを探す（部分一致）
    vote_section = None
    for key in export:
        if "投票集計" in str(key):
            vote_section = export[key]
            break

    if not vote_section or not isinstance(vote_section, dict):
        return None

    # BUY/SELL 票を探す
    action_votes = 0
    safe_votes = 0
    for key, value in vote_section.items():
        key_str = str(key)
        if isinstance(value, (int, float)):
            if any(k in key_str for k in ("BUY票", "SELL票")) and "NOT_" not in key_str:
                action_votes = int(value)
            elif any(k in key_str for k in ("NOT_BUY", "NOT_SELL")):
                safe_votes = int(value)

    if action_votes > 0 or safe_votes > 0:
        return action_votes, safe_votes

    return None


def _infer_votes_from_export(export: dict, decision_raw: str) -> tuple[int, int] | None:
    """
    EXPORT yaml のレーン構造から投票数を推定する（フォールバック）。

    投票ルール（Discussion project）:
    - AGREED レーン: supported_side に 2 票
    - DISAGREED レーン: 各 side に 1 票ずつ（split）
    """
    # 入力セクションからレーン構造を取得
    input_section = export.get("入力", {})
    if not isinstance(input_section, dict):
        return None

    agreed_lanes = input_section.get("一致レーン", [])
    disagreed_lanes = input_section.get("不一致レーン", [])

    if not isinstance(agreed_lanes, list):
        agreed_lanes = []
    if not isinstance(disagreed_lanes, list):
        disagreed_lanes = []

    # レーン別結果から各レーンの支持側を取得
    lane_results = export.get("レーン別結果", {})
    if not isinstance(lane_results, dict):
        lane_results = {}

    action_votes = 0
    safe_votes = 0

    # AGREED レーン: 2 票
    for lane_num in agreed_lanes:
        lane_key = f"set{lane_num}"
        lane_data = lane_results.get(lane_key, {})
        side = lane_data.get("支持側", "") if isinstance(lane_data, dict) else ""
        side_clean = str(side).strip("*").strip()

        if side_clean in ("BUY", "SELL"):
            action_votes += 2
        else:
            safe_votes += 2

    # DISAGREED レーン: 各 side に 1 票ずつ
    for _ in disagreed_lanes:
        action_votes += 1
        safe_votes += 1

    if action_votes > 0 or safe_votes > 0:
        return action_votes, safe_votes

    return None


def _extract_votes_from_text(text: str) -> tuple[int, int] | None:
    """
    本文テキストから投票数をフォールバック抽出する。

    パターン:
    - "BUY票: N" / "NOT_BUY_WAIT票: N"
    - "SELL票: N" / "NOT_SELL_HOLD票: N"
    - "BUYx票 vs NOT_BUY_WAITy票" 形式
    """
    # BUY モード
    m_buy = re.search(r"BUY票[:\s]*(\d+)", text)
    m_notbuy = re.search(r"NOT_BUY_WAIT票[:\s]*(\d+)", text)
    if m_buy and m_notbuy:
        return int(m_buy.group(1)), int(m_notbuy.group(1))

    # SELL モード
    m_sell = re.search(r"SELL票[:\s]*(\d+)", text)
    m_notsell = re.search(r"NOT_SELL_HOLD票[:\s]*(\d+)", text)
    if m_sell and m_notsell:
        return int(m_sell.group(1)), int(m_notsell.group(1))

    # "BUY1票 vs NOT_BUY_WAIT3票" 形式
    m = re.search(r"BUY\s*(\d+)\s*票\s*vs\s*NOT_BUY_WAIT\s*(\d+)\s*票", text)
    if m:
        return int(m.group(1)), int(m.group(2))

    return None


def _extract_decision_from_text(text: str) -> str | None:
    """
    本文マークダウンから判定結果をフォールバック抽出する。

    パターン:
    - 支持側（機械）: **NOT_BUY_WAIT**
    - supported_side: NOT_BUY_WAIT
    """
    m = re.search(
        r"(?:支持側|supported_side)(?:（機械）)?[:\s]*\*{0,2}(\S+?)\*{0,2}\s*$",
        text,
        re.MULTILINE,
    )
    if m:
        return m.group(1).strip("*").strip()
    return None


def _extract_agreement_from_text(text: str) -> str | None:
    """本文マークダウンから総合一致度をフォールバック抽出"""
    m = re.search(
        r"(?:総合一致度|overall_agreement)[:\s]*\*{0,2}(\S+?)\*{0,2}\s*$",
        text,
        re.MULTILINE,
    )
    if m:
        return m.group(1).strip("*").strip()
    return None


def _extract_decision_basis(export: dict | None, full_text: str) -> list[DecisionBasis]:
    """
    根拠リストを抽出する。

    EXPORT yaml の「根拠」キーから取得し、
    各項目内の F#[S#] パターンがあれば fact_id/source_id に分解する。
    """
    basis_list: list[DecisionBasis] = []
    raw_items: list[str] = []

    # EXPORT から取得
    if export and "根拠" in export:
        items = export["根拠"]
        if isinstance(items, list):
            raw_items = [str(item) for item in items]

    # EXPORT に無ければ本文の「根拠（要約）」セクションから
    if not raw_items:
        m = re.search(r"根拠（要約）[:\s]*\n((?:\s+\d+\.\s+.+\n?)+)", full_text)
        if m:
            for line in m.group(1).strip().splitlines():
                cleaned = re.sub(r"^\s*\d+\.\s*", "", line).strip()
                if cleaned:
                    raw_items.append(cleaned)

    for item in raw_items:
        # F#[S#] パターンを探す
        fact_match = re.search(r"(F\d+)", item)
        source_match = re.search(r"(S\d+)", item)
        basis_list.append(DecisionBasis(
            fact_id=fact_match.group(1) if fact_match else "",
            source_id=source_match.group(1) if source_match else "",
            text=item,
        ))

    return basis_list


def parse_final_judge(log_path: Path) -> ParsedJudgment:
    """
    final_judge ログを解析して ParsedJudgment を返す。

    解析優先順位:
    1. EXPORT yaml ブロック（構造化されていて確実）
    2. 本文マークダウン（フォールバック）

    Args:
        log_path: final_judge ログファイルのパス

    Returns:
        ParsedJudgment
    """
    text = log_path.read_text(encoding="utf-8")

    # ティッカーをファイル名から推定
    m_ticker = re.match(r"(.+?)_final_judge_\d+\.md$", log_path.name)
    ticker = m_ticker.group(1) if m_ticker else log_path.stem

    # EXPORT yaml を抽出
    export = _extract_export_yaml(text)

    # --- 判定結果 ---
    decision_raw = None
    if export:
        decision_raw = _extract_decision_from_export(export)
    if not decision_raw:
        decision_raw = _extract_decision_from_text(text)
    if not decision_raw:
        decision_raw = "UNKNOWN"

    decision = DECISION_MAP.get(decision_raw, decision_raw)

    # --- 総合一致度 ---
    agreement = None
    if export:
        agreement = _extract_agreement_from_export(export)
    if not agreement:
        agreement = _extract_agreement_from_text(text)
    if not agreement:
        agreement = "UNKNOWN"

    # --- 投票数 ---
    votes = None
    if export:
        votes = _extract_votes_from_export(export)
    if not votes:
        votes = _extract_votes_from_text(text)
    if not votes and export:
        votes = _infer_votes_from_export(export, decision_raw)

    vote_for = votes[0] if votes else 0
    vote_against = votes[1] if votes else 0

    # --- 根拠 ---
    basis = _extract_decision_basis(export, text)

    # --- ログ日時 ---
    log_date = datetime.fromtimestamp(os.path.getmtime(log_path))

    return ParsedJudgment(
        ticker=ticker,
        decision=decision,
        decision_raw=decision_raw,
        vote_for=vote_for,
        vote_against=vote_against,
        overall_agreement=agreement,
        log_date=log_date,
        decision_basis=basis,
        raw_text=text,
    )
