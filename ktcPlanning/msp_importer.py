"""
MSP XML Importer — msp_importer.py
====================================
Imports a Microsoft Project (.xml) file into the project models.

What gets imported
------------------
  Project          → Project + Revision (Rev 0, is_baseline=True)
  Calendars        → Calendar + WorkingInterval + CalendarException
  Tasks (summary)  → WBSNode + WBSNodeVersion  (OutlineLevel used to build tree)
  Tasks (leaf)     → Task + TaskVersion          (linked to WBS parent)
  Resources        → Resource  (type mapped from MSP Type field)
  Assignments      → Assignment  (task ↔ resource links)
  Predecessors     → Dependency  (FS/SS/FF/SF + lag in hours)

MSP XML structure reference
----------------------------
  <Project>
    <Calendars><Calendar>...</Calendar></Calendars>
    <Tasks><Task>...</Task></Tasks>
    <Resources><Resource>...</Resource></Resources>
    <Assignments><Assignment>...</Assignment></Assignments>
  </Project>

Usage
-----
  # As a standalone call (e.g. from a view or management command):
  from .msp_importer import import_msp_xml

  with open("plan.xml", "rb") as f:
      result = import_msp_xml(f, project_name="Bridge Project", user=request.user)

  # result is a dict:
  # {
  #   "project_id": "...",
  #   "revision_id": "...",
  #   "tasks": 42,
  #   "wbs_nodes": 8,
  #   "resources": 5,
  #   "assignments": 30,
  #   "dependencies": 38,
  #   "warnings": ["..."],
  # }
"""

import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta, date
from decimal import Decimal
from typing import IO, Any
import re

from django.db import transaction
from django.contrib.auth import get_user_model

from .models import (
    Project, Revision,
    Calendar, WorkingInterval, CalendarException,
    WBSNode, WBSNodeVersion,
    Task, TaskVersion,
    Resource,
    Assignment,
    Dependency,
)

User = get_user_model()

# ─── MSP namespace ────────────────────────────────────────────────────────────
# MSP 2003+ XML uses this namespace
MSP_NS = "http://schemas.microsoft.com/project"
NS = {"m": MSP_NS}


def _tag(name: str) -> str:
    """Return fully-qualified tag name."""
    return f"{{{MSP_NS}}}{name}"


def _find(el, path: str):
    """Find a child using the MSP namespace prefix."""
    return el.find(f"m:{path}", NS)


def _text(el, path: str, default=None):
    """Extract text of a child element, return default if missing."""
    node = _find(el, path)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _int(el, path: str, default=0) -> int:
    v = _text(el, path)
    try:
        return int(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _decimal(el, path: str, default=Decimal("0")) -> Decimal:
    v = _text(el, path)
    try:
        return Decimal(str(v)) if v is not None else default
    except Exception:
        return default


def _bool(el, path: str, default=False) -> bool:
    v = _text(el, path)
    if v is None:
        return default
    return v.strip() in ("1", "true", "True")


def _parse_dt(raw: str | None) -> datetime | None:
    """Parse MSP datetime strings like '2026-06-01T08:00:00' or '2026-06-01'."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_duration(raw: str | None) -> Decimal:
    """
    Convert MSP ISO 8601 duration string to hours.
    Examples: 'PT8H' → 8.0,  'P1DT4H' → 12.0,  'P5D' → 40.0 (5 × 8 h/day)
    """
    if not raw:
        return Decimal("0")
    raw = raw.strip()
    total_hours = Decimal("0")
    # Extract days, hours, minutes
    days    = re.search(r"(\d+(?:\.\d+)?)D", raw)
    hours   = re.search(r"(\d+(?:\.\d+)?)H", raw)
    minutes = re.search(r"(\d+(?:\.\d+)?)M", raw)
    if days:
        total_hours += Decimal(days.group(1)) * 8  # MSP uses 8 h/day by default
    if hours:
        total_hours += Decimal(hours.group(1))
    if minutes:
        total_hours += Decimal(minutes.group(1)) / 60
    return total_hours


# MSP DayType → Python weekday (Mon=0 … Sun=6)
# MSP: 1=Sun 2=Mon 3=Tue 4=Wed 5=Thu 6=Fri 7=Sat
_MSP_DAY_TO_PYTHON = {1: 6, 2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5}

# MSP Resource Type → our Resource type string
_MSP_RESOURCE_TYPE = {
    "0": Resource.WORK if hasattr(Resource, "WORK") else "LABOR",
    "1": "MATERIAL",
    "2": "COST",
}
# Map MSP type 0 (Work) to LABOR since that's what the model defines
_MSP_RESOURCE_TYPE["0"] = "LABOR"

# MSP Predecessor link type → our Dependency type
_MSP_LINK_TYPE = {"0": "FF", "1": "FS", "2": "SF", "3": "SS"}


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

@transaction.atomic
def import_msp_xml(
    xml_file: IO[bytes],
    project_name: str | None = None,
    user: Any = None,
) -> dict:
    """
    Parse an MSP XML file and persist everything into the database.

    Parameters
    ----------
    xml_file    : file-like object (binary) pointing to the .xml export
    project_name: override the project name (defaults to MSP <Name> field)
    user        : the Django User doing the import (stored as created_by)

    Returns
    -------
    dict with counts and any non-fatal warnings.
    """
    warnings: list[str] = []

    tree = ET.parse(xml_file)
    root = tree.getroot()

    # Strip namespace from root if present (some exports omit it)
    if not root.tag.startswith("{"):
        # plain XML without namespace — patch helpers
        global NS, MSP_NS
        MSP_NS = ""
        NS = {}

    # ── 1. Project ────────────────────────────────────────────────────────────
    msp_name      = _text(root, "Name") or "Imported Project"
    msp_start     = _parse_dt(_text(root, "StartDate") or _text(root, "Start"))
    msp_finish    = _parse_dt(_text(root, "FinishDate") or _text(root, "Finish"))

    name = project_name or msp_name

    project = Project.objects.create(
        name=name,
        created_by=user,
        start_date=msp_start,
        end_date=msp_finish,
    )

    # create_revision_zero signal fires automatically → fetch it
    revision = project.revisions.get(number=0)
    # Update revision date boundaries from MSP
    revision.project_start = msp_start or revision.project_start
    revision.project_end   = msp_finish
    revision.description   = f"Imported from MSP: {msp_name}"
    revision.save()

    # ── 2. Calendars ──────────────────────────────────────────────────────────
    # msp_uid → Calendar object map (for task calendar references)
    calendar_map: dict[str, Calendar] = {}

    calendars_el = _find(root, "Calendars")
    if calendars_el is not None:
        for cal_el in calendars_el.findall(f"m:Calendar", NS):
            uid        = _text(cal_el, "UID")
            cal_name   = _text(cal_el, "Name") or f"Calendar {uid}"
            is_base    = _bool(cal_el, "IsBaseCalendar")

            cal = Calendar.objects.create(
                project=project,
                name=cal_name,
                is_default=is_base,
            )
            if uid:
                calendar_map[uid] = cal

            # Working weeks → WorkingInterval rows
            week_days_el = cal_el.find("m:WeekDays", NS)
            if week_days_el is not None:
                for wd_el in week_days_el.findall("m:WeekDay", NS):
                    day_type   = _int(wd_el, "DayType")
                    is_working = _bool(wd_el, "DayWorking")
                    python_day = _MSP_DAY_TO_PYTHON.get(day_type)

                    if python_day is None or not is_working:
                        continue

                    # Working times for this day
                    wt_el = wd_el.find("m:WorkingTimes", NS)
                    if wt_el is not None:
                        for wtime_el in wt_el.findall("m:WorkingTime", NS):
                            from_str = _text(wtime_el, "FromTime")
                            to_str   = _text(wtime_el, "ToTime")
                            try:
                                from_t = datetime.strptime(from_str, "%H:%M:%S").time() if from_str else time(8, 0)
                                to_t   = datetime.strptime(to_str,   "%H:%M:%S").time() if to_str   else time(17, 0)
                            except ValueError:
                                from_t, to_t = time(8, 0), time(17, 0)

                            WorkingInterval.objects.create(
                                calendar=cal,
                                weekday=python_day,
                                start_time=from_t,
                                end_time=to_t,
                            )
                    else:
                        # Default 08:00–17:00 if MSP didn't specify
                        WorkingInterval.objects.create(
                            calendar=cal,
                            weekday=python_day,
                            start_time=time(8, 0),
                            end_time=time(17, 0),
                        )

            # Exceptions (holidays / special days)
            exceptions_el = cal_el.find("m:Exceptions", NS)
            if exceptions_el is not None:
                for exc_el in exceptions_el.findall("m:Exception", NS):
                    exc_from = _parse_dt(_text(exc_el, "TimePeriod/FromDate"))
                    exc_to   = _parse_dt(_text(exc_el, "TimePeriod/ToDate"))
                    exc_name = _text(exc_el, "Name") or ""
                    exc_work = _bool(exc_el, "DayWorking")

                    if exc_from is None:
                        continue

                    # Expand date range (MSP exceptions can span multiple days)
                    current_date = exc_from.date()
                    end_date     = exc_to.date() if exc_to else exc_from.date()
                    while current_date <= end_date:
                        CalendarException.objects.get_or_create(
                            calendar=cal,
                            date=current_date,
                            defaults={"is_working": exc_work, "description": exc_name[:255]},
                        )
                        current_date += timedelta(days=1)

    # Pick a default calendar for tasks that don't specify one
    default_calendar = (
        Calendar.objects.filter(project=project, is_default=True).first()
        or Calendar.objects.filter(project=project).first()
    )

    # ── 3. Tasks → WBS tree + leaf Tasks ─────────────────────────────────────
    tasks_el = _find(root, "Tasks")
    if tasks_el is None:
        return _result(project, revision, 0, 0, 0, 0, 0,
                       warnings + ["No <Tasks> element found in XML."])

    msp_tasks = tasks_el.findall("m:Task", NS)

    # First pass: collect all task metadata indexed by UID
    # MSP uses OutlineLevel (1-based depth) to express hierarchy,
    # and OutlineNumber (e.g. "1.2.3") to express position.
    task_meta: dict[str, dict] = {}
    for t_el in msp_tasks:
        uid            = _text(t_el, "UID")
        if uid == "0":          # UID 0 is the project summary row — skip
            continue
        task_meta[uid] = {
            "el":            t_el,
            "uid":           uid,
            "name":          _text(t_el, "Name") or f"Task {uid}",
            "outline_level": _int(t_el, "OutlineLevel", 1),
            "outline_num":   _text(t_el, "OutlineNumber") or "",
            "is_summary":    _bool(t_el, "Summary"),
            "is_milestone":  _bool(t_el, "Milestone"),
            "start":         _parse_dt(_text(t_el, "Start")),
            "finish":        _parse_dt(_text(t_el, "Finish")),
            "duration_hrs":  _parse_duration(_text(t_el, "Duration")),
            "calendar_uid":  _text(t_el, "CalendarUID"),
            "wbs":           _text(t_el, "WBS") or "",
            "constraint_type": _int(t_el, "ConstraintType", 0),
            "predecessors":  t_el.findall("m:PredecessorLink", NS),
        }

    # Build parent lookup: outline_num "1.2.3" → parent is "1.2"
    def parent_outline(outline: str) -> str:
        parts = outline.rsplit(".", 1)
        return parts[0] if len(parts) > 1 else ""

    # Map outline_num → uid for fast parent resolution
    outline_to_uid = {v["outline_num"]: k for k, v in task_meta.items()}

    # Second pass: create WBSNode/WBSNodeVersion for summary rows,
    # and Task/TaskVersion for leaf rows.
    # We process in outline order (MSP XML already orders them top-down).

    # uid → WBSNodeVersion (for summary tasks)
    wbs_version_map: dict[str, WBSNodeVersion] = {}
    # uid → Task (for leaf tasks)
    task_obj_map: dict[str, Task] = {}

    wbs_count  = 0
    task_count = 0

    # Track sequence numbers per parent
    sequence_tracker: dict[str | None, int] = {}

    for uid, meta in task_meta.items():
        outline_num   = meta["outline_num"]
        parent_outline_num = parent_outline(outline_num)
        parent_uid    = outline_to_uid.get(parent_outline_num)

        # Resolve parent WBSNodeVersion
        parent_wbs_version = wbs_version_map.get(parent_uid) if parent_uid else None

        # Sequence within same parent
        seq_key = parent_uid
        sequence_tracker[seq_key] = sequence_tracker.get(seq_key, 0) + 1
        seq = sequence_tracker[seq_key]

        # Resolve calendar
        task_cal = calendar_map.get(meta["calendar_uid"] or "") or default_calendar

        if meta["is_summary"]:
            # ── Summary task → WBS ───────────────────────────────────────────
            wbs_node = WBSNode.objects.create(project=project)
            wbs_ver  = WBSNodeVersion(
                node          = wbs_node,
                revision      = revision,
                parent        = parent_wbs_version,
                title         = meta["name"],
                sequence      = seq,
                planned_start = meta["start"],
                planned_finish= meta["finish"],
            )
            wbs_ver.save()  # full_clean() is called inside save()
            wbs_version_map[uid] = wbs_ver
            wbs_count += 1

        else:
            # ── Leaf task → Task + TaskVersion ───────────────────────────────
            # Must be placed under a WBS node — use nearest summary ancestor
            if parent_wbs_version is None:
                # No summary parent: create an implicit root WBS node
                root_wbs_key = "__root__"
                if root_wbs_key not in wbs_version_map:
                    root_node = WBSNode.objects.create(project=project)
                    root_ver  = WBSNodeVersion(
                        node     = root_node,
                        revision = revision,
                        parent   = None,
                        title    = name,
                        sequence = 1,
                    )
                    root_ver.save()
                    wbs_version_map[root_wbs_key] = root_ver
                    wbs_count += 1
                parent_wbs_version = wbs_version_map[root_wbs_key]

            task_obj = Task.objects.create(project=project)
            task_ver = TaskVersion(
                task           = task_obj,
                revision       = revision,
                wbs_node       = parent_wbs_version,
                title          = meta["name"],
                calendar       = task_cal,
                planned_start  = meta["start"],
                planned_finish = meta["finish"],
                duration_hours = meta["duration_hrs"],
            )
            # Skip full_clean to avoid start==finish on milestones; save directly
            TaskVersion.save(task_ver)
            task_obj_map[uid]  = task_obj
            task_count        += 1

    # ── 4. Resources ──────────────────────────────────────────────────────────
    resources_el = _find(root, "Resources")
    resource_map: dict[str, Resource] = {}  # msp uid → Resource
    res_count = 0

    if resources_el is not None:
        for r_el in resources_el.findall("m:Resource", NS):
            uid      = _text(r_el, "UID")
            if uid in ("0", None):
                continue
            res_name = _text(r_el, "Name") or f"Resource {uid}"
            res_type_raw = _text(r_el, "Type") or "0"
            res_type = _MSP_RESOURCE_TYPE.get(res_type_raw, "LABOR")
            res_code = _text(r_el, "Initials") or f"R{uid}"
            max_units = _decimal(r_el, "MaxUnits", Decimal("1")) * 100  # MSP stores as 0–1

            # Use get_or_create to avoid duplicate code conflicts
            res, created = Resource.objects.get_or_create(
                code=res_code,
                defaults={
                    "name":          res_name,
                    "resource_type": res_type,
                    "max_units":     max_units,
                },
            )
            if not created:
                # Resource with this code already exists — update name if blank
                warnings.append(
                    f"Resource code '{res_code}' already exists; reusing existing record."
                )
            resource_map[uid] = res
            if created:
                res_count += 1

    # ── 5. Assignments ────────────────────────────────────────────────────────
    assignments_el = _find(root, "Assignments")
    assign_count = 0

    if assignments_el is not None:
        for a_el in assignments_el.findall("m:Assignment", NS):
            task_uid     = _text(a_el, "TaskUID")
            res_uid      = _text(a_el, "ResourceUID")
            units_raw    = _decimal(a_el, "Units", Decimal("1"))
            units_pct    = units_raw * 100          # MSP: 0.0–1.0 → our: 0–100
            actual_work  = _parse_duration(_text(a_el, "ActualWork"))
            planned_work = _parse_duration(_text(a_el, "Work"))

            task_obj = task_obj_map.get(task_uid or "")
            resource = resource_map.get(res_uid or "")

            if not task_obj or not resource:
                warnings.append(
                    f"Assignment skipped: task_uid={task_uid} res_uid={res_uid} not found."
                )
                continue

            Assignment.objects.get_or_create(
                revision=revision,
                task=task_obj,
                resource=resource,
                defaults={
                    "units_percent": units_pct,
                    "planned_hours": planned_work,
                    "actual_hours":  actual_work,
                },
            )
            assign_count += 1

    # ── 6. Dependencies (Predecessor links) ───────────────────────────────────
    dep_count = 0

    for uid, meta in task_meta.items():
        successor_task = task_obj_map.get(uid)
        if not successor_task:
            continue  # summary tasks don't get dependency rows

        for pred_el in meta["predecessors"]:
            pred_uid  = _text(pred_el, "PredecessorUID")
            link_type = _text(pred_el, "Type") or "1"   # default FS
            lag_raw   = _text(pred_el, "LinkLag")        # in tenths of a minute in MSP

            predecessor_task = task_obj_map.get(pred_uid or "")
            if not predecessor_task:
                warnings.append(
                    f"Dependency skipped: predecessor UID={pred_uid} is a summary/missing task."
                )
                continue

            dep_type = _MSP_LINK_TYPE.get(link_type, "FS")

            # MSP LinkLag is in tenths of a minute → convert to hours
            try:
                lag_hours = int(round(int(lag_raw) / 600)) if lag_raw else 0
            except (ValueError, TypeError):
                lag_hours = 0

            _, created = Dependency.objects.get_or_create(
                revision=revision,
                predecessor=predecessor_task,
                successor=successor_task,
                defaults={
                    "dependency_type": dep_type,
                    "lag_hours":       lag_hours,
                },
            )
            if created:
                dep_count += 1

    return _result(
        project, revision,
        task_count, wbs_count, res_count, assign_count, dep_count,
        warnings,
    )


def _result(project, revision, tasks, wbs, resources, assignments, deps, warnings):
    return {
        "project_id":   str(project.pk),
        "revision_id":  str(revision.pk),
        "project_name": project.name,
        "tasks":        tasks,
        "wbs_nodes":    wbs,
        "resources":    resources,
        "assignments":  assignments,
        "dependencies": deps,
        "warnings":     warnings,
    }
