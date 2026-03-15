"""
用語集メンテナンス

glossary テーブルのエントリを走査し、term や aliases が重複するエントリを検出・統合する。

統合ルール：
- 全エントリの term と aliases を小文字で正規化して比較
- 同じ正規化ワードを持つエントリ同士をグループ化（Union-Find）
- 2件以上のグループを統合対象とする
- 最小 id のエントリに統合：aliases をマージ、explanation は長い方を採用
- 他のエントリを削除
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    fetch_all_glossary,
    update_glossary_entry,
    delete_glossary_entry,
)


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: int, y: int) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1


def _normalize(text: str) -> str:
    return text.strip().lower()


def find_duplicate_groups(entries: list[dict]) -> list[list[dict]]:
    """重複するエントリをグループ化して返す。"""
    if not entries:
        return []

    n = len(entries)
    uf = _UnionFind(n)

    word_to_idx: dict[str, int] = {}
    for i, entry in enumerate(entries):
        words = [_normalize(entry["term"])]
        for alias in (entry.get("aliases") or []):
            words.append(_normalize(alias))

        for w in words:
            if not w:
                continue
            if w in word_to_idx:
                uf.union(i, word_to_idx[w])
            else:
                word_to_idx[w] = i

    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = uf.find(i)
        groups.setdefault(root, []).append(i)

    return [
        [entries[i] for i in indices]
        for indices in groups.values()
        if len(indices) >= 2
    ]


def merge_entries(group: list[dict]) -> tuple[dict, list[int]]:
    """
    グループ内のエントリを1つに統合する。

    Returns:
        (統合後のエントリ, 削除対象の id リスト)
    """
    sorted_group = sorted(group, key=lambda e: e["id"])
    base = sorted_group[0]
    others = sorted_group[1:]

    seen_normalized: set[str] = {_normalize(base["term"])}
    merged_aliases: list[str] = []

    all_alias_candidates = []
    for entry in sorted_group:
        if entry["id"] != base["id"]:
            all_alias_candidates.append(entry["term"])
        for alias in (entry.get("aliases") or []):
            all_alias_candidates.append(alias)

    for alias in all_alias_candidates:
        norm = _normalize(alias)
        if norm and norm not in seen_normalized:
            seen_normalized.add(norm)
            merged_aliases.append(alias)

    best_explanation = max(
        (e["explanation"] for e in sorted_group),
        key=len,
    )

    merged = {
        "id": base["id"],
        "term": base["term"],
        "explanation": best_explanation,
        "aliases": merged_aliases,
    }
    delete_ids = [e["id"] for e in others]
    return merged, delete_ids


def run_glossary_consolidation(dry_run: bool = False) -> dict:
    """用語集の重複統合を実行する。"""
    print("[NightWorker] 用語集メンテナンス開始")

    entries = safe_db(fetch_all_glossary)
    if not entries:
        print("  glossary エントリなし。スキップ。")
        return {"groups_found": 0, "merged": 0, "deleted": 0}

    print(f"  glossary エントリ数：{len(entries)}")

    groups = find_duplicate_groups(entries)
    if not groups:
        print("  重複なし。スキップ。")
        return {"groups_found": 0, "merged": 0, "deleted": 0}

    print(f"  重複グループ：{len(groups)}件")

    total_deleted = 0
    for i, group in enumerate(groups, 1):
        terms = [e["term"] for e in group]
        print(f"\n  グループ{i}：{terms}")

        merged, delete_ids = merge_entries(group)
        print(f"    統合先：#{merged['id']} ({merged['term']})")
        print(f"    aliases：{merged['aliases']}")
        print(f"    削除対象：{delete_ids}")

        if dry_run:
            print(f"    [dry-run] スキップ")
            continue

        safe_db(
            update_glossary_entry,
            entry_id=merged["id"],
            term=merged["term"],
            explanation=merged["explanation"],
            aliases=merged["aliases"],
        )
        for did in delete_ids:
            safe_db(delete_glossary_entry, entry_id=did)
        total_deleted += len(delete_ids)
        print(f"    統合完了")

    result = {
        "groups_found": len(groups),
        "merged": len(groups),
        "deleted": total_deleted,
    }
    print(f"\n[NightWorker] 用語集メンテナンス完了"
          f"（統合：{len(groups)}件、削除：{total_deleted}件）")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="用語集メンテナンス")
    parser.add_argument("--dry-run", action="store_true",
                        help="統合を実行せずプレビューのみ")
    args = parser.parse_args()
    run_glossary_consolidation(dry_run=args.dry_run)
