"""
Analyzerプロジェクト ログ解析モジュール

Analyzer の final_judge ログから判定・投票・根拠を抽出する。
EXPORT yamlブロックを優先的にパースし、フォールバックとして本文マークダウンを解析する。
"""
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml


# Analyzer → Planning の用語変換テーブル（5択スタンス対応）
DECISION_MAP: dict[str, str] = {
    "BUY": "BUY",
    "SELL": "SELL",
    "ADD": "ADD",
    "REDUCE": "REDUCE",
    "HOLD": "HOLD",
}


@dataclass
class DecisionBasis:
    """判定の根拠1件"""
    lane: str            # "set1" 等（どのレーンの議論由来か）
    source_desc: str     # ソースの説明
    source_url: str      # ソース URL（証拠表から取得。なければ空文字）
    text: str            # 主張の要約テキスト


@dataclass
class ParsedJudgment:
    """final_judge ログから抽出したデータ"""
    ticker: str
    decision: str                       # "BUY" | "SELL" | "ADD" | "REDUCE" | "HOLD"
    decision_raw: str                   # DECISION_MAP 適用前の生値
    vote_for: int                       # アクション側の票数
    vote_against: int                   # 安全側の票数
    overall_agreement: str              # "AGREED_STRONG" | "MIXED" | "INCOMPLETE"
    log_date: datetime                  # ログ作成日時
    decision_basis: list[DecisionBasis] # 根拠リスト
    raw_text: str                       # ログ全文（エージェントに渡す用）


@dataclass
class AnalyzerLogs:
    """指定銘柄のログファイル一式"""
    final_judge: Path | None = None
    set_files: list[Path] = field(default_factory=list)
    judge_files: list[Path] = field(default_factory=list)


def find_analyzer_logs(analyzer_dir: Path, ticker: str) -> AnalyzerLogs:
    """
    セッションディレクトリから指定銘柄のログファイルを探索する。

    対象ファイル（7件）:
    - {TICKER}_set1.md, _set2.md, _set3.md  → set_files（議論ログ）
    - {TICKER}_set{N}_judge_{K}.md           → judge_files（判定）
    - {TICKER}_final_judge_{K}.md            → final_judge（最大番号を採用）
    """
    result = AnalyzerLogs()
    t = ticker.upper()

    if not analyzer_dir.exists():
        return result

    final_judges = sorted(analyzer_dir.glob(f"{t}_final_judge_*.md"))
    if final_judges:
        result.final_judge = final_judges[-1]

    result.set_files = sorted(
        p for p in analyzer_dir.glob(f"{t}_set*.md")
        if re.search(r"_set\d+\.md$", p.name)
    )

    result.judge_files = sorted(
        p for p in analyzer_dir.glob(f"{t}_set*_judge_*.md")
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

    5択スタンス対応:
      action = BUY + SELL + ADD + REDUCE
      safe   = HOLD
    """
    # 投票集計キーを探す（部分一致）
    vote_section = None
    for key in export:
        if "投票集計" in str(key):
            vote_section = export[key]
            break

    if not vote_section or not isinstance(vote_section, dict):
        return None

    action_votes = 0
    safe_votes = 0
    for key, value in vote_section.items():
        key_upper = str(key).upper()
        if not isinstance(value, (int, float)):
            continue
        v = int(value)
        # Action stances: BUY, SELL, ADD, REDUCE
        if any(s in key_upper for s in ("BUY", "SELL", "ADD", "REDUCE")):
            action_votes += v
        # Safe stance: HOLD
        elif "HOLD" in key_upper:
            safe_votes += v

    if action_votes > 0 or safe_votes > 0:
        return action_votes, safe_votes

    return None


def _infer_votes_from_export(export: dict, decision_raw: str) -> tuple[int, int] | None:
    """
    EXPORT yaml のレーン構造から投票数を推定する（フォールバック）。

    投票ルール（Analyzer project）:
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

    5択スタンス対応:
    - "BUY: N" / "HOLD: N" / "BUY票: N" / "HOLD票: N"
    """
    action_total = 0
    safe_total = 0
    found = False

    for stance in ("BUY", "SELL", "ADD", "REDUCE"):
        m = re.search(rf"{stance}[票]?[:\s]*(\d+)", text)
        if m:
            action_total += int(m.group(1))
            found = True

    m = re.search(r"HOLD[票]?[:\s]*(\d+)", text)
    if m:
        safe_total += int(m.group(1))
        found = True

    if found:
        return action_total, safe_total

    return None


def _extract_decision_from_text(text: str) -> str | None:
    """
    本文マークダウンから判定結果をフォールバック抽出する。

    パターン:
    - 支持側（機械）: **HOLD**
    - supported_side: BUY
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

    EXPORT yaml の「根拠」キーから取得する。
    新形式（構造化）: lane / claim / source_desc / source_url を持つ dict
    旧形式（テキスト）: 文字列リスト（フォールバック）
    """
    basis_list: list[DecisionBasis] = []

    # EXPORT から取得（"根拠" キー優先、新形式 "reasons" にもフォールバック）
    raw_items = None
    if export:
        raw_items = export.get("根拠") or export.get("reasons")
    if raw_items is not None:
        items = raw_items
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    # 構造化された根拠（"text" キー優先、旧形式 "claim" にもフォールバック）
                    basis_list.append(DecisionBasis(
                        lane=str(item.get("lane", "")),
                        source_desc=str(item.get("source_desc", "")),
                        source_url=str(item.get("source_url", "")),
                        text=str(item.get("text", "") or item.get("claim", "")),
                    ))
                else:
                    # 旧形式フォールバック: テキストのみ
                    basis_list.append(DecisionBasis(
                        lane="",
                        source_desc="",
                        source_url="",
                        text=str(item),
                    ))

    # EXPORT に無ければ本文の「根拠（構造化）」セクションから
    if not basis_list:
        m = re.search(r"根拠[（(](?:構造化|要約)[）)][:\s]*\n((?:\s*-\s+.+\n?)+)", full_text)
        if m:
            for line in m.group(1).strip().splitlines():
                cleaned = re.sub(r"^\s*-\s*", "", line).strip()
                if cleaned:
                    basis_list.append(DecisionBasis(
                        lane="",
                        source_desc="",
                        source_url="",
                        text=cleaned,
                    ))

    return basis_list


def parse_final_judge_from_db(fj_data: dict, ticker: str, created_at: str) -> ParsedJudgment:
    """
    DB の archive.final_judge dict から ParsedJudgment を生成する。

    fj_data の期待キー:
        markdown          : final_judge ログ全文（テキスト解析に使用）
        action_votes      : アクション票数（なければ markdown から抽出）
        safe_votes        : 安全票数（同上）
        overall_agreement : 総合一致度（同上）
    """
    text = fj_data.get("markdown", "")

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

    # --- 総合一致度（DB優先、なければテキストから） ---
    agreement = fj_data.get("overall_agreement")
    if not agreement:
        if export:
            agreement = _extract_agreement_from_export(export)
    if not agreement:
        agreement = _extract_agreement_from_text(text)
    if not agreement:
        agreement = "UNKNOWN"

    # --- 投票数（DB優先、なければテキストから） ---
    vote_for = fj_data.get("action_votes")
    vote_against = fj_data.get("safe_votes")
    if vote_for is None or vote_against is None:
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
    try:
        log_date = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        log_date = log_date.replace(tzinfo=None)
    except (ValueError, AttributeError):
        log_date = datetime.now()

    return ParsedJudgment(
        ticker=ticker.upper(),
        decision=decision,
        decision_raw=decision_raw,
        vote_for=int(vote_for),
        vote_against=int(vote_against),
        overall_agreement=agreement,
        log_date=log_date,
        decision_basis=basis,
        raw_text=text,
    )


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
