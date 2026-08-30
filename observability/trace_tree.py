"""
observability/trace_tree.py - v1.0 (Session 11)

Trace ID 기반 의사결정 경로 트리 시각화.
SignalPipeline → RiskCheck → OrderExecution 전체 경로 추적.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.logger import setup_logger

logger = setup_logger("trace_tree")

_MAX_NODES_PER_TRACE = 200
_MAX_TRACES = 1000


@dataclass
class TraceNode:
    """단일 추적 노드 (의사결정 경로의 한 단계)"""
    node_id: str
    trace_id: str
    parent_id: Optional[str]
    module_name: str
    operation: str
    input_summary: str
    output_summary: str
    duration_ms: float
    success: bool
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "module_name": self.module_name,
            "operation": self.operation,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "duration_ms": round(self.duration_ms, 3),
            "success": self.success,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class TraceTree:
    """
    Trace ID 기반 의사결정 경로 트리.

    사용 예::

        tree = TraceTree()
        tree.add_node(TraceNode(
            node_id="n1", trace_id="T-001", parent_id=None,
            module_name="SignalPipeline", operation="process",
            input_summary="ticker=005930", output_summary="BUY score=0.72",
            duration_ms=12.5, success=True,
        ))
        print(tree.to_text_tree("T-001"))
    """

    def __init__(self, max_traces: int = _MAX_TRACES) -> None:
        self._max_traces = max_traces
        self._traces: Dict[str, List[TraceNode]] = {}
        self._trace_order: deque = deque(maxlen=max_traces)
        self._node_index: Dict[str, TraceNode] = {}

    def add_node(self, node: TraceNode) -> None:
        """
        노드를 트리에 추가.

        trace_id가 새로 등장하면 자동으로 trace를 생성.
        trace당 최대 노드 수(_MAX_NODES_PER_TRACE)를 초과하면 무시.
        """
        try:
            tid = node.trace_id
            if tid not in self._traces:
                if len(self._traces) >= self._max_traces:
                    oldest = self._trace_order[0]
                    if oldest in self._traces:
                        for n in self._traces[oldest]:
                            self._node_index.pop(n.node_id, None)
                        del self._traces[oldest]
                self._traces[tid] = []
                self._trace_order.append(tid)

            if len(self._traces[tid]) >= _MAX_NODES_PER_TRACE:
                logger.warning(f"TraceTree: trace {tid} 노드 한도 초과, 무시")
                return

            self._traces[tid].append(node)
            self._node_index[node.node_id] = node

        except Exception as e:
            logger.warning(f"TraceTree.add_node 실패: {e}")

    def get_tree(self, trace_id: str) -> List[TraceNode]:
        """특정 trace의 모든 노드를 추가 순서(루트→리프)로 반환."""
        return list(self._traces.get(trace_id, []))

    def get_node(self, node_id: str) -> Optional[TraceNode]:
        """node_id로 단일 노드 조회."""
        return self._node_index.get(node_id)

    def to_text_tree(self, trace_id: str) -> str:
        """텍스트 기반 트리 시각화."""
        nodes = self.get_tree(trace_id)
        if not nodes:
            return ""

        children: Dict[Optional[str], List[TraceNode]] = defaultdict(list)
        for n in nodes:
            children[n.parent_id].append(n)

        lines: List[str] = [f"Trace: {trace_id}"]

        def _render(node: TraceNode, depth: int) -> None:
            indent = "  " * depth
            status = "✅" if node.success else "❌"
            lines.append(
                f"{indent}{status} [{node.module_name}] {node.operation} "
                f"({node.duration_ms:.1f}ms)"
            )
            if node.input_summary:
                lines.append(f"{indent}   IN:  {node.input_summary}")
            if node.output_summary:
                lines.append(f"{indent}   OUT: {node.output_summary}")
            if node.error:
                lines.append(f"{indent}   ERR: {node.error}")
            for child in children.get(node.node_id, []):
                _render(child, depth + 1)

        for root in children.get(None, []):
            _render(root, 0)

        return "\n".join(lines)

    def critical_path(self, trace_id: str) -> List[TraceNode]:
        """가장 느린 경로(루트→리프까지 duration_ms 합산 최대) 반환."""
        nodes = self.get_tree(trace_id)
        if not nodes:
            return []

        children: Dict[Optional[str], List[TraceNode]] = defaultdict(list)
        for n in nodes:
            children[n.parent_id].append(n)

        best_path: List[TraceNode] = []
        best_duration: float = -1.0

        def _dfs(node: TraceNode, current_path: List[TraceNode], current_duration: float) -> None:
            nonlocal best_path, best_duration
            new_path = current_path + [node]
            new_duration = current_duration + node.duration_ms
            child_list = children.get(node.node_id, [])
            if not child_list:
                if new_duration > best_duration:
                    best_duration = new_duration
                    best_path = new_path
            else:
                for child in child_list:
                    _dfs(child, new_path, new_duration)

        for root in children.get(None, []):
            _dfs(root, [], 0.0)

        return best_path

    def total_duration_ms(self, trace_id: str) -> float:
        """trace의 총 소요 시간 (모든 노드 duration_ms 합산)."""
        return sum(n.duration_ms for n in self.get_tree(trace_id))

    def failed_nodes(self, trace_id: str) -> List[TraceNode]:
        """특정 trace에서 실패한 노드만 반환."""
        return [n for n in self.get_tree(trace_id) if not n.success]

    def summary(self, trace_id: str) -> dict:
        """trace 요약 통계 반환."""
        nodes = self.get_tree(trace_id)
        if not nodes:
            return {"trace_id": trace_id, "total_nodes": 0}

        success_count = sum(1 for n in nodes if n.success)
        slowest = max(nodes, key=lambda n: n.duration_ms)
        modules = list(dict.fromkeys(n.module_name for n in nodes))

        return {
            "trace_id": trace_id,
            "total_nodes": len(nodes),
            "success_count": success_count,
            "fail_count": len(nodes) - success_count,
            "total_duration_ms": round(self.total_duration_ms(trace_id), 3),
            "slowest_operation": f"{slowest.module_name}.{slowest.operation} ({slowest.duration_ms:.1f}ms)",
            "modules_involved": modules,
        }

    def all_trace_ids(self) -> List[str]:
        """현재 보관 중인 모든 trace ID 목록."""
        return list(self._traces.keys())

    def clear(self, trace_id: Optional[str] = None) -> None:
        """trace 삭제 (trace_id 없으면 전체 삭제)."""
        if trace_id is not None:
            if trace_id in self._traces:
                for n in self._traces[trace_id]:
                    self._node_index.pop(n.node_id, None)
                del self._traces[trace_id]
        else:
            self._traces.clear()
            self._node_index.clear()
            self._trace_order.clear()
