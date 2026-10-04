# -*- coding: utf-8 -*-
"""observability/trace_bridge.py - 의사결정 경로 트리 브리지 (P8-4).

배경
----
`observability/trace_tree.py`(218줄, TraceTree/TraceNode)는
작성되어 있었으나 **프로덕션 참조 0건의 고아 모듈**이었다.
(문서에 'TraceTree'가 등장해 감사에서 놓치기 쉬운 유형)

역할
----
1. 전역 `TraceTree` 싱글턴 제공 (`get_trace_tree()`)
2. 스테이지 기록 헬퍼 `record_stage()` — 실패해도 프로덕션 무영향
3. 조회 편의: `get_trace_text(trace_id)`, `latest_trace()`, `recent_summaries()`

배선
----
- `SignalPipeline.process()`가 파이프라인 1단계를 기록
- 텔레그램 `/trace` 명령, HTML 대시보드 섹션에서 조회
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.logger import setup_logger
from observability.trace_tree import TraceNode, TraceTree

logger = setup_logger("trace_bridge")

MAX_TRACES = 200
_tree: Optional[TraceTree] = None
_seq: int = 0


def get_trace_tree() -> TraceTree:
    """전역 TraceTree(지연 생성)."""
    global _tree
    if _tree is None:
        _tree = TraceTree(max_traces=MAX_TRACES)
    return _tree


def record_stage(
    trace_id: str,
    module_name: str,
    operation: str,
    input_summary: str = "",
    output_summary: str = "",
    duration_ms: float = 0.0,
    success: bool = True,
    error: Optional[str] = None,
    parent_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """의사결정 경로에 한 단계를 기록한다(실패는 조용히 무시)."""
    global _seq
    try:
        if not trace_id:
            return None
        _seq += 1
        node = TraceNode(
            node_id=f"n{_seq}",
            trace_id=str(trace_id),
            parent_id=parent_id,
            module_name=str(module_name),
            operation=str(operation),
            input_summary=str(input_summary)[:200],
            output_summary=str(output_summary)[:200],
            duration_ms=float(duration_ms),
            success=bool(success),
            error=error,
            metadata=metadata or {},
        )
        get_trace_tree().add_node(node)
        return node.node_id
    except Exception as e:                       # 방어적: 추적이 본류를 깨면 안 된다
        logger.debug(f"trace 기록 실패(무시): {e}")
        return None


def get_trace_text(trace_id: str) -> str:
    """트리를 사람이 읽는 텍스트로 반환(없으면 안내 문구)."""
    try:
        tree = get_trace_tree()
        if trace_id not in tree.all_trace_ids():
            return f"해당 trace_id를 찾을 수 없습니다: {trace_id}"
        return tree.to_text_tree(trace_id)
    except Exception as e:
        return f"trace 조회 실패: {e}"


def latest_trace_id() -> Optional[str]:
    """가장 최근 trace_id."""
    try:
        ids = get_trace_tree().all_trace_ids()
        return ids[-1] if ids else None
    except Exception:
        return None


def recent_summaries(limit: int = 5) -> List[Dict[str, Any]]:
    """최근 trace 요약 목록(대시보드용)."""
    out: List[Dict[str, Any]] = []
    try:
        tree = get_trace_tree()
        for tid in tree.all_trace_ids()[-limit:]:
            try:
                s = tree.summary(tid)
                s.setdefault("trace_id", tid)
                out.append(s)
            except Exception as e:
                logger.debug(f"{tid} 요약 실패(무시): {e}")
    except Exception as e:
        logger.debug(f"trace 요약 목록 실패(무시): {e}")
    return list(reversed(out))


def stage_timer() -> float:
    """경과(ms) 계산용 시작 시각(perf_counter)."""
    return time.perf_counter()


def elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def clear() -> None:
    """트리 초기화(테스트/운영 편의)."""
    global _tree
    _tree = None


def _main() -> int:
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="의사결정 경로 트리 조회")
    parser.add_argument("trace_id", nargs="?", help="trace_id (생략 시 최근)")
    args = parser.parse_args()

    tid = args.trace_id or latest_trace_id()
    if not tid:
        print("기록된 trace가 없습니다(시그널 발생 후 조회 가능).")
        return 1
    print(get_trace_text(tid))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
