"""
موتور CPM (Critical Path Method) — متصل به موتور تقویم
──────────────────────────────────────────────────────────
وظیفه: برای یک Revision کامل، روی گراف وابستگی‌ها دو پاس اجرا کند:
  - Forward pass  → Early Start / Early Finish هر تسک (با تقویم کاری)
  - Backward pass → Late Start / Late Finish هر تسک (با تقویم کاری)
  - محاسبه Total Float و Free Float (ساعت کاری خالص)
  - مشخص کردن مسیر بحرانی (is_critical)
نتیجه در TaskScheduleMetrics و TaskVersion ذخیره می‌شود.

اتصال به تقویم:
  - هر TaskVersion می‌تواند calendar مخصوص خود داشته باشد
  - اگر calendar نداشت، از calendar پیش‌فرض پروژه استفاده می‌شود
  - اگر هیچ تقویمی نبود، فرض ۸ ساعت روز / ۵ روز هفته

پشتیبانی از نوع وابستگی:
  FS (Finish-Start)  : پیش‌فرض — متداول‌ترین
  SS (Start-Start)   : هر دو باید با هم شروع شوند
  FF (Finish-Finish) : هر دو باید با هم تمام شوند
  SF (Start-Finish)  : نادر — شروع اولی پایان دومی را تعیین می‌کند

lag_hours روی هر وابستگی اعمال می‌شود (مثبت = تاخیر، منفی = جلو افتادن).
"""

from __future__ import annotations

import datetime
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .calendar import CalendarEngine

if TYPE_CHECKING:
    from .models import Revision, TaskVersion, Dependency, Calendar

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# ساختار داخلی گراف
# ─────────────────────────────────────────

@dataclass
class TaskNode:
    """نمایش یک TaskVersion در گراف CPM."""
    tv_id: int
    task_id: str
    duration_hours: float
    calendar_id: int | None          # برای پیدا کردن CalendarEngine مناسب

    # این دو توسط CPM پر می‌شوند، ورودی نیستند
    planned_start:  datetime.datetime | None = None
    planned_finish: datetime.datetime | None = None

    early_start:  datetime.datetime | None = None
    early_finish: datetime.datetime | None = None
    late_start:   datetime.datetime | None = None
    late_finish:  datetime.datetime | None = None

    total_float_hours: int = 0
    free_float_hours:  int = 0
    is_critical: bool = False

    successors:   list[EdgeInfo] = field(default_factory=list)
    predecessors: list[EdgeInfo] = field(default_factory=list)


@dataclass
class EdgeInfo:
    """یک وابستگی در گراف."""
    from_task_id: str
    to_task_id: str
    dep_type: str    # FS / SS / FF / SF
    lag_hours: int


# ─────────────────────────────────────────
# موتور اصلی
# ─────────────────────────────────────────

class CPMEngine:
    """
    استفاده:
        engine = CPMEngine(revision)
        result = engine.run()
        print(result.summary())
    """

    def __init__(self, revision: Revision):
        self.revision = revision
        self.nodes: dict[str, TaskNode] = {}       # task_id → TaskNode
        self.tv_by_task: dict[str, int] = {}       # task_id → task_version_id
        self._cal_engines: dict[int, CalendarEngine] = {}   # calendar_id → engine
        self._default_cal_engine: CalendarEngine | None = None
        self._project_start: datetime.datetime | None = None
        self._project_finish: datetime.datetime | None = None

    # ─── بارگذاری تقویم‌ها ──────────────────

    def _load_calendars(self) -> None:
        """
        همه تقویم‌های مورد نیاز این revision را یک‌بار لود می‌کند.
        هر CalendarEngine intervals و exceptions را در حافظه نگه می‌دارد.
        """
        from .models import Calendar

        # تقویم پیش‌فرض پروژه
        default_cal = (
            Calendar.objects
            .filter(project=self.revision.project, is_default=True)
            .prefetch_related("intervals", "exceptions")
            .first()
        )
        if default_cal:
            self._default_cal_engine = CalendarEngine(default_cal)
            self._cal_engines[default_cal.id] = self._default_cal_engine

        # تقویم‌های مخصوص تسک‌ها
        cal_ids = set(
            n.calendar_id for n in self.nodes.values()
            if n.calendar_id and n.calendar_id not in self._cal_engines
        )
        if cal_ids:
            cals = (
                Calendar.objects

.filter(id__in=cal_ids)
                .prefetch_related("intervals", "exceptions")
            )
            for cal in cals:
                self._cal_engines[cal.id] = CalendarEngine(cal)

    def _get_engine(self, node: TaskNode) -> CalendarEngine | None:
        """تقویم مناسب برای یک تسک — اول مخصوص، بعد پیش‌فرض، بعد None."""
        if node.calendar_id and node.calendar_id in self._cal_engines:
            return self._cal_engines[node.calendar_id]
        return self._default_cal_engine

    # ─── بارگذاری داده ──────────────────────

    def _load(self) -> None:
        """
        بارگذاری TaskVersion‌ها و Dependency‌ها از DB به حافظه.
        planned_start/finish دیگر از DB خوانده نمی‌شوند —
        CPM آن‌ها را از project_start محاسبه و می‌نویسد.
        """
        from .models import TaskVersion, Dependency

        versions = TaskVersion.objects.filter(
            revision=self.revision,
            is_deleted=False,
        ).values("id", "task_id", "duration_hours", "calendar_id")

        for v in versions:
            tid = str(v["task_id"])
            self.nodes[tid] = TaskNode(
                tv_id=v["id"],
                task_id=tid,
                duration_hours=float(v["duration_hours"]),
                calendar_id=v["calendar_id"],
            )
            self.tv_by_task[tid] = v["id"]

        deps = Dependency.objects.filter(revision=self.revision).values(
            "predecessor_id", "successor_id", "dependency_type", "lag_hours"
        )

        for d in deps:
            pred_id = str(d["predecessor_id"])
            succ_id = str(d["successor_id"])
            if pred_id not in self.nodes or succ_id not in self.nodes:
                logger.warning(
                    "CPM: وابستگی با task_id ناموجود نادیده گرفته شد: %s → %s",
                    pred_id, succ_id,
                )
                continue

            edge = EdgeInfo(
                from_task_id=pred_id,
                to_task_id=succ_id,
                dep_type=d["dependency_type"],
                lag_hours=d["lag_hours"],
            )
            self.nodes[pred_id].successors.append(edge)
            self.nodes[succ_id].predecessors.append(edge)

    # ─── ترتیب توپولوژیک (Kahn's algorithm) ───

    def _topological_sort(self) -> list[str]:
        """ترتیب توپولوژیک گراف — اگر حلقه وجود داشته باشد خطا می‌دهد."""
        in_degree: dict[str, int] = {tid: 0 for tid in self.nodes}
        for node in self.nodes.values():
            for edge in node.successors:
                in_degree[edge.to_task_id] += 1

        queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while queue:
            tid = queue.popleft()
            order.append(tid)
            for edge in self.nodes[tid].successors:
                in_degree[edge.to_task_id] -= 1
                if in_degree[edge.to_task_id] == 0:
                    queue.append(edge.to_task_id)

        if len(order) != len(self.nodes):
            cycle_nodes = [t for t in self.nodes if t not in set(order)]
            raise ValueError(
                f"CPM: حلقه در گراف وابستگی‌ها شناسایی شد. "
                f"تسک‌های درگیر: {cycle_nodes[:5]}"
            )

        return order

    # ─── توابع کمکی تقویم ───────────────────

    def _add_hours(self, node: TaskNode, start: datetime.datetime, hours: float) -> datetime.datetime:
        """
        از start به اندازه hours ساعت کاری جلو برو.
        از CalendarEngine تسک استفاده می‌کند — اگر نداشت timedelta ساده.
        """
        engine = self._get_engine(node)
        if engine:
            snapped = engine.next_working_moment(start)
            return engine.add_working_hours(snapped, hours)
        # فال‌بک: بدون تقویم — ساعت خام
        return start + datetime.timedelta(hours=hours)

    def _subtract_hours(self, node: TaskNode, finish: datetime.datetime, hours: float) -> datetime.datetime:
        """
        از finish به اندازه hours ساعت کاری عقب برو.
        برای backward pass استفاده می‌شود.
        تقویم کاری را در نظر می‌

گیرد.
        """
        engine = self._get_engine(node)
        if engine:
            return engine.subtract_working_hours(finish, hours)
        return finish - datetime.timedelta(hours=hours)

    def _working_hours_between(
        self, node: TaskNode,
        start: datetime.datetime,
        finish: datetime.datetime,
    ) -> float:
        """ساعت کاری خالص بین دو لحظه — برای محاسبه float."""
        engine = self._get_engine(node)
        if engine:
            return engine.working_hours_between(start, finish)
        return (finish - start).total_seconds() / 3600

    def _add_lag(self, node: TaskNode, dt: datetime.datetime, lag_hours: int) -> datetime.datetime:
        """اعمال lag با احترام به تقویم."""
        if lag_hours == 0:
            return dt
        engine = self._get_engine(node)
        if engine and lag_hours > 0:
            return engine.add_working_hours(dt, lag_hours)
        if engine and lag_hours < 0:
            return engine.subtract_working_hours(dt, abs(lag_hours))
        return dt + datetime.timedelta(hours=lag_hours)

    # ─── محاسبه تاثیر وابستگی روی تاریخ ───

    def _earliest_start_from_edge(self, pred: TaskNode, edge: EdgeInfo) -> datetime.datetime:
        """زودترین لحظه‌ای که successor می‌تواند شروع کند."""
        succ = self.nodes[edge.to_task_id]

        if edge.dep_type == "FS":
            base = pred.early_finish
            return self._add_lag(succ, base, edge.lag_hours)

        elif edge.dep_type == "SS":
            base = pred.early_start
            return self._add_lag(succ, base, edge.lag_hours)

        elif edge.dep_type == "FF":
            # EF_succ >= EF_pred + lag  →  ES_succ = EF_pred + lag - duration_succ
            ef_pred_lagged = self._add_lag(pred, pred.early_finish, edge.lag_hours)
            return self._subtract_hours(succ, ef_pred_lagged, succ.duration_hours)

        elif edge.dep_type == "SF":
            # EF_succ >= ES_pred + lag  →  ES_succ = ES_pred + lag - duration_succ
            es_pred_lagged = self._add_lag(pred, pred.early_start, edge.lag_hours)
            return self._subtract_hours(succ, es_pred_lagged, succ.duration_hours)

        return self._add_lag(succ, pred.early_finish, edge.lag_hours)

    def _latest_finish_from_edge(self, succ: TaskNode, edge: EdgeInfo) -> datetime.datetime:
        """دیرترین لحظه‌ای که predecessor می‌تواند تمام شود."""
        pred = self.nodes[edge.from_task_id]

        if edge.dep_type == "FS":
            # LF_pred <= LS_succ - lag
            return self._add_lag(pred, succ.late_start, -edge.lag_hours)

        elif edge.dep_type == "SS":
            # LS_pred <= LS_succ - lag  →  LF_pred = LS_succ - lag + duration_pred
            ls_succ_lagged = self._add_lag(pred, succ.late_start, -edge.lag_hours)
            return self._add_hours(pred, ls_succ_lagged, pred.duration_hours)

        elif edge.dep_type == "FF":
            # LF_pred <= LF_succ - lag
            return self._add_lag(pred, succ.late_finish, -edge.lag_hours)

        elif edge.dep_type == "SF":
            # ES_pred <= EF_succ - lag  →  LF_pred = LF_succ - lag + duration_pred
            lf_succ_lagged = self._add_lag(pred, succ.late_finish, -edge.lag_hours)
            return self._add_hours(pred, lf_succ_lagged, pred.duration_hours)

        return self._add_lag(pred, succ.late_start, -edge.lag_hours)

    # ─── Forward Pass ───────────────────────

    def _forward_pass(self, order: list[str]) -> None:
        """
        از ابتدا به انتها — محاسبه Early Start و Early Finish.
        تسک‌های بدون predecessor از revision.project_start شروع می‌کنند.
        early_finish با موتور تقویم هر تسک محاسبه می‌شود.
        """
        anchor = self.revision.project_start

        for tid in order:
            node = self.nodes[tid]

            if not node.predecessors:
                node.early_start = anchor
            else:
                candidates = [
                    self._earliest_start_from_edge(self.nodes[e.from_task_id], e)
                    for e in node.predecessors
                ]
                node.early_start = max(candidates)

            # snap به اولین لحظه کاری + محاسبه finish با تقویم
            node.early_finish = self._add_hours(node, node.early_start, node.duration_hours)

        self._project_finish = max(n.early_finish for n in self.nodes.values())
        self._project_start  = min(n.early_start  for n in self.nodes.values())

    # ─── Backward Pass ──────────────────────

    def backward_pass(self, order: list[str]) -> None:
        """
        از انتها به ابتدا — محاسبه Late Start و Late Finish.
        late_start با موتور تقویم هر تسک محاسبه می‌شود.
        """
        for tid in reversed(order):
            node = self.nodes[tid]

            if not node.successors:
                node.late_finish = self._project_finish
            else:
                candidates = [
                    self._latest_finish_from_edge(self.nodes[e.to_task_id], e)
                    for e in node.successors
                ]
                node.late_finish = min(candidates)

            # عقب رفتن از late_finish به اندازه duration با تقویم
            node.late_start = self._subtract_hours(node, node.late_finish, node.duration_hours)

    # ─── محاسبه Float و Critical Path ───────

    def _compute_floats(self) -> None:
        """
        Total Float = ساعت کاری بین ES و LS (نه timedelta خام)
        Free Float  = ساعت کاری بین EF و ES_successor (کمترین)
        is_critical = total_float <= 0
        """
        for tid, node in self.nodes.items():
            # total float با تقویم — ساعت کاری خالص
            node.total_float_hours = int(
                self._working_hours_between(node, node.early_start, node.late_start)
            )
            node.is_critical = node.total_float_hours <= 0

            if node.successors:
                free_floats = []
                for edge in node.successors:
                    succ = self.nodes[edge.to_task_id]
                    ff = self._working_hours_between(node, node.early_finish, succ.early_start)
                    free_floats.append(ff)
                node.free_float_hours = int(min(free_floats))
            else:
                node.free_float_hours = node.total_float_hours

    # ─── ذخیره نتایج در DB ──────────────────

    def _save_metrics(self) -> None:
        """
        دو کار انجام می‌دهد:
        ۱. planned_start و planned_finish را روی TaskVersion می‌نویسد
        ۲. TaskScheduleMetrics را با bulk ذخیره می‌کند
        """
        from .models import TaskScheduleMetrics, TaskVersion

        # ── ۱. نوشتن planned_start/finish روی TaskVersion ──
        tv_ids = [node.tv_id for node in self.nodes.values()]
        tv_map = {tv.id: tv for tv in TaskVersion.objects.filter(id__in=tv_ids)}

        tv_to_update = []
        for node in self.nodes.values():
            tv = tv_map.get(node.tv_id)
            if tv:
                tv.planned_start  = node.early_start
                tv.planned_finish = node.early_finish
                tv_to_update.append(tv)

        TaskVersion.objects.bulk_update(tv_to_update, ["planned_start", "planned_finish"])

        # ── ۲. ذخیره TaskScheduleMetrics ──
        to_create = []
        to_update = []

        existing = {
            m.task_version_id: m
            for m in TaskScheduleMetrics.objects.filter(
                task_version__revision=self.revision
            )
        }

        for node in self.nodes.values():
            tv_id = node.tv_id
            data = dict(
                early_start=node.early_start,
                early_finish=node.early_finish,
                late_start=node.late_start,
                late_finish=node.late_finish,
                total_float_hours=node.total_float_hours,
                free_float_hours=node.free_float_hours,
                is_critical=node.is_critical,
            )
            if tv_id in existing:
                m = existing[tv_id]
                for k, v in data.items():
                    setattr(m, k, v)
                to_update.append(m)
            else:
                to_create.append(TaskScheduleMetrics(task_version_id=tv_id, **data))

        if to_create:
            TaskScheduleMetrics.objects.bulk_create(to_create)
        if to_update:
            TaskScheduleMetrics.objects.bulk_update(to_update, [
                "early_start", "early_finish",
                "late_start", "late_finish",
                "total_float_hours", "free_float_hours",
                "is_critical",
            ])

        logger.info(
            "CPM: revision=%s — %d TaskVersion بروز، %d metrics ایجاد، %d metrics بروز",
            self.revision.number, len(tv_to_update), len(to_create), len(to_update),
        )

    # ─── رابط عمومی ─────────────────────────

    def run(self) -> CPMResult:
        """اجرای کامل موتور CPM روی revision."""
        self._load()

        if not self.nodes:
            logger.warning("CPM: revision=%s هیچ تسکی ندارد.", self.revision.number)
            return CPMResult(revision=self.revision, nodes={})

        self._load_calendars()        # ← تقویم‌ها بعد از load تسک‌ها
        order = self._topological_sort()
        self._forward_pass(order)
        self._backward_pass(order)
        self._compute_floats()
        self._save_metrics()

        return CPMResult(revision=self.revision, nodes=self.nodes)

    def critical_path(self) -> list[int]:
        return [n.tv_id for n in self.nodes.values() if n.is_critical]

    @property
    def project_duration_days(self) -> float | None:
        if self._project_start and self._project_finish:
            return (self._project_finish - self._project_start).days
        return None


# ─────────────────────────────────────────
# نتیجه اجرا
# ─────────────────────────────────────────

@dataclass
class CPMResult:
    revision: Revision
    nodes: dict[str, TaskNode]

    def summary(self) -> dict:
        if not self.nodes:
            return {"total_tasks": 0}

        critical = [n for n in self.nodes.values() if n.is_critical]
        return {
            "total_tasks": len(self.nodes),
            "critical_tasks": len(critical),
            "project_start":  min(n.early_start  for n in self.nodes.values()),
            "project_finish": max(n.early_finish for n in self.nodes.values()),
            "critical_path_ids": [n.tv_id for n in critical],
        }


# ─────────────────────────────────────────
# تابع کمکی سطح بالا
# ─────────────────────────────────────────

def run_cpm(revision: Revision) -> CPMResult:
    """
    اجرای CPM برای یک revision و ذخیره نتایج.

    مثال:
        result = run_cpm(revision)
        print(result.summary())
        # {'total_tasks': 42, 'critical_tasks': 8, ...}
    """
    return CPMEngine(revision).run()