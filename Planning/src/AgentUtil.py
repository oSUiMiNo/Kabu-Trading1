"""
Claude Agent SDK ユーティリティ（ラッパー）

ロジック本体は shared/agent_util.py に統合。
このファイルは PROJECT_ROOT / SRC_DIR を束縛して再エクスポートする。
"""
import sys
from pathlib import Path

_SHARED = str(Path(__file__).resolve().parent.parent.parent / "shared")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

_MODULE_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = Path(__file__).resolve().parent

from agent_util import (  # noqa: E402
    AgentResult,
    save_result_log,
    side_ja,
    extract_text,
    extract_cost,
    extract_tool_use,
    parse_agent_file,
)
from agent_util import (  # noqa: E402
    load_debug_config as _load_debug_config,
    call_agent as _call_agent,
)


def load_debug_config(phase: str) -> dict:
    return _load_debug_config(phase, project_root=_MODULE_ROOT)


async def call_agent(messages, **kwargs):
    return await _call_agent(messages, project_root=_MODULE_ROOT, src_dir=_SRC_DIR, **kwargs)


if __name__ == "__main__":
    import asyncio

    if len(sys.argv) < 2:
        print("Usage: python AgentUtil.py <prompt> [file_path]")
        sys.exit(1)

    prompt = sys.argv[1]
    file_path = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(call_agent(prompt, file_path=file_path))
