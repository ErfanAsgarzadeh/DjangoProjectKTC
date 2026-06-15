from __future__ import annotations

import datetime
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .calendar import CalendarEngine

if TYPE_CHECKING:
    from .models import Revision, TaskVersion, Dependency, TaskActual, WBSNodeVersion

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# Graph Node
# ─────────────────────────────────────────

@dataclass
class TaskNode:
    tv_id: int
    task_id: str
    duration_hours: float
    calendar_id: int | None

    early_start: datetime.datetime | None = None
    early_finish: datetime.datetime | None = None
    late_start: datetime.datetime | None = None
    late_finish: datetime.datetime | None = None

    total_float_hours: float = 0
    free_float_hours: float = 0
    is_critical: bool = False

    successors: list["EdgeInfo"] = field(default_factory=list)
    predecessors: list["EdgeInfo"] = field(default_factory=list)

    actual: "TaskActual | None" = None  # ← مهم


@dataclass
class EdgeInfo:
    from_task_id: str
    to_task_id: str
    dep_type: str
    lag_hours: float


# ─────────────────────────────────────────
# CPM Engine
# ─────────────────────────────────────────

class CPMEngine:

    def __init__(self, revision: Revision):
        self.revision = revision

        self.nodes: dict[str, TaskNode] = {}
        self._cal_engines: dict[int, CalendarEngine] = {}
        self._default_cal_engine: CalendarEngine | None = None

        self._project_start: datetime.datetime | None = None
        self._project_finish: datetime.datetime | None = None

    # ─────────────────────────────
    # Freeze logic (TaskActual)
    # ─────────────────────────────

    def _is_frozen(self, node: TaskNode) -> bool:
        """
        یه تسک فریز می‌شه اگر:
        - تموم شده باشه (actual_finish ست شده یا progress == 100)
        - در حال اجرا باشه (actual_start ست شده ولی هنوز تموم نشده)
        """
        if not node.actual:
            return False
        return (
            node.actual.actual_finish is not None
            or node.actual.progress >= 100
            or node.actual.actual_start is not None
        )

    # ─────────────────────────────
    # Load data
    # ─────────────────────────────

    def _load(self) -> None:
        from .models import TaskVersion, Dependency, TaskActual

        versions = TaskVersion.objects.filter(
            revision=self.revision,
            is_deleted=False
        ).values("id", "task_id", "duration_hours", "calendar_id")

        actual_map = {
            a.task_version_id: a
            for a in TaskActual.objects.filter(task_version__revision=self.revision)
        }

        for v in versions:
            tid = str(v["task_id"])

            self.nodes[tid] = TaskNode(
                tv_id=v["id"],
                task_id=tid,
                duration_hours=float(v["duration_hours"]),
                calendar_id=v["calendar_id"],
                actual=actual_map.get(v["id"]),
            )

        deps = Dependency.objects.filter(revision=self.revision).values(
            "predecessor_id", "successor_id", "dependency_type", "lag_hours"
        )

        for d in deps:
            pred = str(d["predecessor_id"])
            succ = str(d["successor_id"])

            if pred not in self.nodes or succ not in self.nodes:
                continue

            edge = EdgeInfo(
                from_task_id=pred,
                to_task_id=succ,
                dep_type=d["dependency_type"],
                lag_hours=float(d["lag_hours"]),
            )

            self.nodes[pred].successors.append(edge)
            self.nodes[succ].predecessors.append(edge)

    # ─────────────────────────────
    # Topological sort
    # ─────────────────────────────

    def _topological_sort(self) -> list[str]:
        in_degree = {k: 0 for k in self.nodes}

        for n in self.nodes.values():
            for e in n.successors:
                in_degree[e.to_task_id] += 1

        queue = deque([k for k, v in in_degree.items() if v == 0])
        order = []

        while queue:
            tid = queue.popleft()
            order.append(tid)

            for e in self.nodes[tid].successors:
                in_degree[e.to_task_id] -= 1
                if in_degree[e.to_task_id] == 0:
                    queue.append(e.to_task_id)

        if len(order) != len(self.nodes):
            raise ValueError("Cycle detected in CPM graph")

        return order

    # ─────────────────────────────
    # Calendar helpers
    # ─────────────────────────────

    def _get_engine(self, node: TaskNode) -> CalendarEngine | None:
        return self._cal_engines.get(node.calendar_id, self._default_cal_engine)

    def _add_hours(self, node, start, hours):
        engine = self._get_engine(node)
        if engine:
            return engine.add_working_hours(start, hours)
        return start + datetime.timedelta(hours=hours)

    def _subtract_hours(self, node, end, hours):
        engine = self._get_engine(node)
        if engine:
            return engine.subtract_working_hours(end, hours)
        return end - datetime.timedelta(hours=hours)

    # ─────────────────────────────
    # Forward pass
    # ─────────────────────────────

    def _forward_pass(self, order: list[str]) -> None:
        anchor = self.revision.project_start

        for tid in order:
            node = self.nodes[tid]

            if self._is_frozen(node):
                node.early_start = node.actual.actual_start
                if node.actual.actual_finish is not None:
                    # تسک کاملاً تموم شده
                    node.early_finish = node.actual.actual_finish
                else:
                    # تسک در حال اجراست — EF رو از actual_start + duration محاسبه می‌کنیم
                    node.early_finish = self._add_hours(node, node.actual.actual_start, node.duration_hours)
                continue

            if not node.predecessors:
                es = anchor
            else:
                es_candidates = []

                for e in node.predecessors:
                    pred = self.nodes[e.from_task_id]
                    es_candidates.append(pred.early_finish)

                es = max(es_candidates)

            node.early_start = es
            node.early_finish = self._add_hours(node, es, node.duration_hours)

        self._project_start = min(n.early_start for n in self.nodes.values())
        self._project_finish = max(n.early_finish for n in self.nodes.values())

    # ─────────────────────────────
    # Backward pass
    # ─────────────────────────────

    def _backward_pass(self, order: list[str]) -> None:
        for tid in reversed(order):
            node = self.nodes[tid]

            if self._is_frozen(node):
                node.late_start = node.actual.actual_start
                # برای تسک‌های in-progress که actual_finish ندارن، از early_finish استفاده می‌کنیم
                node.late_finish = node.actual.actual_finish or node.early_finish
                continue

            if not node.successors:
                lf = self._project_finish
            else:
                lf = min(
                    self.nodes[e.to_task_id].late_start
                    for e in node.successors
                )

            node.late_finish = lf
            node.late_start = self._subtract_hours(node, lf, node.duration_hours)

    # ─────────────────────────────
    # Floats
    # ─────────────────────────────

    def _compute_floats(self) -> None:
        for node in self.nodes.values():

            node.total_float_hours = (
                (node.late_start - node.early_start).total_seconds() / 3600
                if node.late_start and node.early_start else 0
            )

            node.is_critical = node.total_float_hours <= 0

            if node.successors:
                node.free_float_hours = min(
                    (
                        (self.nodes[e.to_task_id].early_start - node.early_finish)
                        .total_seconds() / 3600
                    )
                    for e in node.successors
                    if node.early_finish and self.nodes[e.to_task_id].early_start
                )
            else:
                node.free_float_hours = node.total_float_hours

    # ─────────────────────────────
    # Run
    # ─────────────────────────────

    def run(self):
        self._load()

        if not self.nodes:
            return {"total_tasks": 0}

        order = self._topological_sort()

        self._forward_pass(order)
        self._backward_pass(order)
        self._compute_floats()

        return {
            "total_tasks": len(self.nodes),
            "critical_tasks": sum(n.is_critical for n in self.nodes.values()),
            "project_start": self._project_start,
            "project_finish": self._project_finish,
        }