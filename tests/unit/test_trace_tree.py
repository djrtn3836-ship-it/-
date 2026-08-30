"""
tests/unit/test_trace_tree.py - v1.0 (Session 11)
TraceTree 단위 테스트 (38개)
"""

import time
import pytest

from observability.trace_tree import TraceNode, TraceTree


def make_node(
    node_id="n1", trace_id="T-001", parent_id=None,
    module_name="SignalPipeline", operation="process",
    input_summary="ticker=005930", output_summary="BUY",
    duration_ms=10.0, success=True, error=None,
):
    return TraceNode(
        node_id=node_id, trace_id=trace_id, parent_id=parent_id,
        module_name=module_name, operation=operation,
        input_summary=input_summary, output_summary=output_summary,
        duration_ms=duration_ms, success=success, error=error,
    )


class TestTraceNode:
    def test_creation(self):
        n = make_node()
        assert n.node_id == "n1"
        assert n.trace_id == "T-001"
        assert n.success is True

    def test_to_dict_keys(self):
        d = make_node().to_dict()
        for k in ["node_id", "trace_id", "parent_id", "module_name",
                   "operation", "duration_ms", "success"]:
            assert k in d

    def test_error_field_none_by_default(self):
        assert make_node().error is None

    def test_error_field_set(self):
        n = make_node(error="DB timeout")
        assert n.error == "DB timeout"

    def test_timestamp_auto_set(self):
        before = time.time()
        n = make_node()
        after = time.time()
        assert before <= n.timestamp <= after

    def test_mutable_dataclass(self):
        n = make_node()
        n.duration_ms = 99.9
        assert n.duration_ms == 99.9


class TestTraceTreeAddAndGet:
    def setup_method(self):
        self.tree = TraceTree()

    def test_get_empty_trace(self):
        assert self.tree.get_tree("nonexistent") == []

    def test_add_single_node(self):
        self.tree.add_node(make_node())
        assert len(self.tree.get_tree("T-001")) == 1

    def test_add_multiple_nodes_same_trace(self):
        self.tree.add_node(make_node("n1", "T-001"))
        self.tree.add_node(make_node("n2", "T-001", parent_id="n1"))
        assert len(self.tree.get_tree("T-001")) == 2

    def test_add_nodes_different_traces(self):
        self.tree.add_node(make_node("n1", "T-001"))
        self.tree.add_node(make_node("n2", "T-002"))
        assert len(self.tree.get_tree("T-001")) == 1
        assert len(self.tree.get_tree("T-002")) == 1

    def test_get_node_by_id(self):
        self.tree.add_node(make_node("n1", "T-001"))
        n = self.tree.get_node("n1")
        assert n is not None
        assert n.node_id == "n1"

    def test_get_node_nonexistent(self):
        assert self.tree.get_node("nonexistent") is None

    def test_all_trace_ids(self):
        self.tree.add_node(make_node("n1", "T-001"))
        self.tree.add_node(make_node("n2", "T-002"))
        ids = self.tree.all_trace_ids()
        assert "T-001" in ids
        assert "T-002" in ids

    def test_order_preserved(self):
        for i in range(5):
            self.tree.add_node(make_node(f"n{i}", "T-001"))
        nodes = self.tree.get_tree("T-001")
        assert [n.node_id for n in nodes] == [f"n{i}" for i in range(5)]


class TestTraceTreeTextTree:
    def test_empty_trace_returns_empty_string(self):
        tree = TraceTree()
        assert tree.to_text_tree("nonexistent") == ""

    def test_single_node_contains_module(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", module_name="SignalPipeline"))
        text = tree.to_text_tree("T-001")
        assert "SignalPipeline" in text

    def test_success_indicator(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", success=True))
        assert "✅" in tree.to_text_tree("T-001")

    def test_failure_indicator(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", success=False, error="timeout"))
        text = tree.to_text_tree("T-001")
        assert "❌" in text

    def test_parent_child_indentation(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", module_name="Root"))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", module_name="Child"))
        text = tree.to_text_tree("T-001")
        lines = text.split("\n")
        root_line = next(l for l in lines if "Root" in l)
        child_line = next(l for l in lines if "Child" in l)
        root_indent = len(root_line) - len(root_line.lstrip())
        child_indent = len(child_line) - len(child_line.lstrip())
        assert child_indent > root_indent


class TestTraceTreeCriticalPath:
    def test_empty_trace_returns_empty(self):
        assert TraceTree().critical_path("nonexistent") == []

    def test_single_node_is_critical_path(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", duration_ms=5.0))
        path = tree.critical_path("T-001")
        assert len(path) == 1
        assert path[0].node_id == "n1"

    def test_linear_chain_full_path(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", duration_ms=10.0))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", duration_ms=5.0))
        tree.add_node(make_node("n3", "T-001", parent_id="n2", duration_ms=3.0))
        path = tree.critical_path("T-001")
        assert len(path) == 3

    def test_branching_selects_slowest(self):
        tree = TraceTree()
        tree.add_node(make_node("root", "T-001", duration_ms=1.0))
        tree.add_node(make_node("fast", "T-001", parent_id="root", duration_ms=2.0))
        tree.add_node(make_node("slow", "T-001", parent_id="root", duration_ms=10.0))
        path = tree.critical_path("T-001")
        node_ids = [n.node_id for n in path]
        assert "slow" in node_ids
        assert "fast" not in node_ids

    def test_critical_path_starts_from_root(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", duration_ms=5.0))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", duration_ms=3.0))
        path = tree.critical_path("T-001")
        assert path[0].node_id == "n1"

    def test_critical_path_ends_at_leaf(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", duration_ms=5.0))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", duration_ms=3.0))
        path = tree.critical_path("T-001")
        assert path[-1].node_id == "n2"


class TestTraceTreeSummary:
    def test_empty_trace_summary(self):
        s = TraceTree().summary("nonexistent")
        assert s["total_nodes"] == 0

    def test_summary_counts(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", success=True, duration_ms=10.0))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", success=False, duration_ms=5.0))
        s = tree.summary("T-001")
        assert s["total_nodes"] == 2
        assert s["success_count"] == 1
        assert s["fail_count"] == 1

    def test_total_duration(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", duration_ms=10.0))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", duration_ms=5.0))
        assert tree.summary("T-001")["total_duration_ms"] == pytest.approx(15.0)

    def test_modules_involved(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", module_name="SignalPipeline"))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", module_name="RiskCheck"))
        s = tree.summary("T-001")
        assert "SignalPipeline" in s["modules_involved"]
        assert "RiskCheck" in s["modules_involved"]

    def test_slowest_operation_in_summary(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", operation="fast_op", duration_ms=2.0))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", operation="slow_op", duration_ms=50.0))
        s = tree.summary("T-001")
        assert "slow_op" in s["slowest_operation"]


class TestTraceTreeUtilities:
    def test_failed_nodes_empty_when_all_success(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", success=True))
        assert tree.failed_nodes("T-001") == []

    def test_failed_nodes_returns_failures(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", success=True))
        tree.add_node(make_node("n2", "T-001", parent_id="n1", success=False))
        failed = tree.failed_nodes("T-001")
        assert len(failed) == 1
        assert failed[0].node_id == "n2"

    def test_total_duration_ms_single(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001", duration_ms=42.0))
        assert tree.total_duration_ms("T-001") == pytest.approx(42.0)

    def test_total_duration_ms_empty(self):
        assert TraceTree().total_duration_ms("nonexistent") == 0.0

    def test_clear_specific_trace(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001"))
        tree.add_node(make_node("n2", "T-002"))
        tree.clear("T-001")
        assert tree.get_tree("T-001") == []
        assert len(tree.get_tree("T-002")) == 1

    def test_clear_all(self):
        tree = TraceTree()
        tree.add_node(make_node("n1", "T-001"))
        tree.add_node(make_node("n2", "T-002"))
        tree.clear()
        assert tree.all_trace_ids() == []

    def test_max_traces_eviction(self):
        tree = TraceTree(max_traces=3)
        for i in range(5):
            tree.add_node(make_node(f"n{i}", f"T-{i:03d}"))
        assert len(tree.all_trace_ids()) <= 3

    def test_add_node_duplicate_node_id_no_exception(self):
        tree = TraceTree()
        tree.add_node(make_node("dup", "T-001"))
        tree.add_node(make_node("dup", "T-001"))
        assert len(tree.get_tree("T-001")) == 2
