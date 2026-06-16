from os import name

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction
from datetime import date, timedelta, datetime
from decimal import Decimal
from collections import defaultdict

from rest_framework.views import APIView

from .cpm import CPMEngine
# ایمپورت تمامی مدل‌های مورد نیاز
from .models import Project, Revision, WBSNodeVersion, TaskVersion, Dependency, TaskRole, Task, WBSNode, TaskReportLog, \
    TaskActual, TaskChatMessage, Assignment, Resource, ResourcePool, ResourceRole, ResourceSkill, ResourceSkillMapping, \
    ResourceException, ResourceRate, VarianceReport
from .serializers import (
    ProjectSerializer,
    RevisionSerializer,
    WbsNodeSerializer,
    ActivityNodeSerializer,
    DependencySerializer,
    TaskRoleSerializer, TaskReportLogSerializer, TaskChatMessageSerializer, ResourcePoolSerializer,
    ResourceRoleSerializer, ResourceSkillSerializer, ResourceSerializer, ResourceSkillMappingSerializer,
    ResourceExceptionSerializer, ResourceRateSerializer, AssignmentSerializer, VarianceReportSerializer
)
from rest_framework.parsers import MultiPartParser, FormParser

from .msp_importer import import_msp_xml
from django.db.models import Max

from .variance_engine import EVMEngine


def check_revision_is_open(revision):
    if revision.approved_at is not None:
        raise PermissionDenied("این نسخه قفل شده است و قابل تغییر نیست.")


class ProjectViewSet(viewsets.ModelViewSet):
    """مدیریت پروژه‌ها"""
    queryset = Project.objects.filter(is_deleted=False).exclude(name='System-Personal-Tasks')
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.save()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class RevisionViewSet(viewsets.ModelViewSet):
    """مدیریت نسخه‌ها (Revisions) با قابلیت فیلتر بر اساس پروژه"""
    queryset = Revision.objects.filter(is_deleted=False ).order_by('-number')
    serializer_class = RevisionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.save()
    # --- متد قفل کردن نسخه ---
    @action(detail=True, methods=['post'], url_path='approve')
    def approve_revision(self, request, pk=None):
        revision = self.get_object()

        if revision.approved_at:
            return Response({"detail": "این نسخه قبلاً تایید و قفل شده است."}, status=status.HTTP_400_BAD_REQUEST)

        revision.approved_by = request.user
        revision.approved_at = timezone.now()
        revision.save()

        return Response({"detail": "نسخه با موفقیت قفل شد."}, status=status.HTTP_200_OK)

    # --- ارسال اطلاعات به گانت‌چارت ---
    @action(detail=True, methods=['get'], url_path='gantt-data')
    def get_gantt_data(self, request, pk=None):
        revision = self.get_object()

        wbs_nodes = WBSNodeVersion.objects.filter(revision=revision, is_deleted=False)
        wbs_serializer = WbsNodeSerializer(wbs_nodes, many=True)

        tasks = TaskVersion.objects.filter(revision=revision, is_deleted=False).select_related('metrics')
        activity_serializer = ActivityNodeSerializer(tasks, many=True)

        nodes = wbs_serializer.data + activity_serializer.data
        dependencies = Dependency.objects.filter(revision=revision)
        dependency_serializer = DependencySerializer(dependencies, many=True)

        return Response({
            "nodes": nodes,
            "dependencies": dependency_serializer.data
        }, status=status.HTTP_200_OK)

    # --- ساخت پیش‌نویس (Draft) از یک نسخه ---
    @action(detail=True, methods=['post'], url_path='create-draft')
    @transaction.atomic
    def create_draft_from_revision(self, request, pk=None):
        base_revision = self.get_object()

        if not base_revision.approved_at:
            return Response(
                {"detail": "نسخه پایه هنوز باز است. ابتدا آن را قفل کنید."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # دریافت و اعتبارسنجی توضیحات (اجباری)
        description = request.data.get('description', '').strip()
        if not description:
            return Response(
                {"detail": "وارد کردن توضیحات (دلیل ساخت پیش‌نویس) الزامی است."},
                status=status.HTTP_400_BAD_REQUEST
            )

        new_revision_number = Revision.objects.filter(project=base_revision.project).count() + 1
        new_revision = Revision.objects.create(
            project=base_revision.project,
            number=new_revision_number,
            description=description,
            project_start=base_revision.project_start,
            created_by=request.user
        )

        old_to_new_wbs_map = {}
        old_wbs_nodes = WBSNodeVersion.objects.filter(
            revision=base_revision, is_deleted=False
        ).order_by('level', 'sequence')

        for old_node in old_wbs_nodes:
            new_parent = None
            if old_node.parent_id:
                new_parent = old_to_new_wbs_map.get(old_node.parent_id)

            new_node = WBSNodeVersion.objects.create(
                node=old_node.node,
                revision=new_revision,
                parent=new_parent,
                title=old_node.title,
                sequence=old_node.sequence
            )
            old_to_new_wbs_map[old_node.id] = new_node

        old_tasks = TaskVersion.objects.filter(revision=base_revision, is_deleted=False)
        new_tasks_to_create = []

        for old_task in old_tasks:
            new_tasks_to_create.append(
                TaskVersion(
                    task=old_task.task,
                    revision=new_revision,
                    wbs_node=old_to_new_wbs_map[old_task.wbs_node_id],
                    title=old_task.title,
                    calendar=old_task.calendar,
                    planned_start=old_task.planned_start,
                    planned_finish=old_task.planned_finish,
                    duration_hours=old_task.duration_hours,
                    weight=old_task.weight,
                    description=old_task.description
                )
            )
        TaskVersion.objects.bulk_create(new_tasks_to_create)

        old_deps = Dependency.objects.filter(revision=base_revision)
        new_deps_to_create = []
        for dep in old_deps:
            new_deps_to_create.append(
                Dependency(
                    revision=new_revision,
                    predecessor=dep.predecessor,
                    successor=dep.successor,
                    dependency_type=dep.dependency_type,
                    lag_hours=dep.lag_hours
                )
            )
        Dependency.objects.bulk_create(new_deps_to_create)

        serializer = self.get_serializer(new_revision)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='run-cpm')
    def run_cpm_engine(self, request, pk=None):
        """
        اجرای موتور محاسباتی زمان‌بندی (CPM) روی یک نسخه خاص
        """
        revision = self.get_object()

        # بررسی اینکه آیا نسخه باز است و قابلیت ویرایش دارد یا خیر
        check_revision_is_open(revision)

        try:
            # اجرای موتور CPM که Early/Late start و finish ها را حساب و ذخیره می‌کند

            engine = CPMEngine(revision)
            cpm_result = engine.run()

            # پس از محاسبه، مستقیماً داده‌های آپدیت‌شده گانت‌چارت را استخراج کرده و برمی‌گردانیم
            # این کار باعث می‌شود فرانت‌اند نیاز به Request دوم نداشته باشد
            return self.get_gantt_data(request, pk=pk)

        except ValueError as e:
            # این خطا معمولاً به خاطر وجود حلقه (Cycle) در گراف وابستگی‌ها پرتاب می‌شود
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {"detail": f"خطای پیش‌بینی نشده در محاسبات CPM: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
class WbsNodeViewSet(viewsets.ModelViewSet):
    queryset = WBSNodeVersion.objects.filter(is_deleted=False)
    serializer_class = WbsNodeSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'node__id'

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_value = self.kwargs[self.lookup_field]

        # گرفتن ریویژن از آدرس در صورت وجود
        revision_id = self.request.query_params.get('revision_id')

        filter_kwargs = {self.lookup_field: lookup_value}
        if revision_id:
            filter_kwargs['revision_id'] = revision_id
        else:
            # پیدا کردن ردیف در نسخه‌ای که هنوز تایید و قفل نشده است
            filter_kwargs['revision__approved_at__isnull'] = True

        # استفاده از first() برای جلوگیری از ارور تعدد ردیف
        obj = queryset.filter(**filter_kwargs).first()

        if not obj:
            from django.http import Http404
            raise Http404("گره WBS در نسخه فعال یافت نشد.")

        self.check_object_permissions(self.request, obj)
        return obj
    def get_queryset(self):
        queryset = super().get_queryset()
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        return queryset

    # --- هندل کردن ساخت صحیح گره WBS ---
    def perform_create(self, serializer):
        revision_id = self.request.data.get('revisionId') or self.request.query_params.get('revision_id')
        if not revision_id:
            raise ValidationError({"revisionId": "آیدی نسخه برای ساخت گره الزامی است."})

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision)

        # پیدا کردن گره والد (در صورت وجود)
        parent_id = self.request.data.get('parentId')
        parent_node = None
        if parent_id:
            parent_node = get_object_or_404(WBSNodeVersion, node_id=parent_id, revision=revision)

        # ---------------- NEW CODE ----------------
        # Calculate the next sequence number for this parent in this revision
        max_seq_dict = WBSNodeVersion.objects.filter(
            revision=revision,
            parent=parent_node
        ).aggregate(Max('sequence'))

        current_max_seq = max_seq_dict.get('sequence__max') or 0
        next_sequence = current_max_seq + 1
        # ------------------------------------------

        base_node = WBSNode.objects.create(project=revision.project)

        # Pass the newly calculated sequence to save()
        serializer.save(
            node=base_node,
            revision=revision,
            parent=parent_node,
            sequence=next_sequence
        )

    def perform_update(self, serializer):
        check_revision_is_open(serializer.instance.revision)
        serializer.save()

    def perform_destroy(self, instance):
        # بررسی قفل نبودن نسخه
        check_revision_is_open(instance.revision)

        # ۱. گرفتن خود گره و تمامی زیرمجموعه‌های آن (فرزندان، نوه‌ها و...) به کمک MPTT
        descendants = instance.get_descendants(include_self=True)

        # ۲. مخفی کردن تمام تسک‌هایی که به این گره‌ها (والد یا فرزندان) متصل هستند
        TaskVersion.objects.filter(
            wbs_node__in=descendants,
            revision=instance.revision
        ).update(is_deleted=True)

        # ۳. مخفی کردن خود گره WBS و تمامی گره‌های فرزند آن به صورت یکجا
        descendants.update(is_deleted=True)


class ActivityNodeViewSet(viewsets.ModelViewSet):
    queryset = TaskVersion.objects.filter(is_deleted=False).select_related('metrics','actual')
    serializer_class = ActivityNodeSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'task__id'

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup_value = self.kwargs[self.lookup_field]

        revision_id = self.request.query_params.get('revision_id')

        filter_kwargs = {self.lookup_field: lookup_value}
        if revision_id:
            filter_kwargs['revision_id'] = revision_id
        else:
            filter_kwargs['revision__approved_at__isnull'] = True

        # انتخاب دقیق همان ردیفی که متعلق به نسخه باز است
        obj = queryset.filter(**filter_kwargs).first()

        if not obj:
            from django.http import Http404
            raise Http404("تسک مورد نظر در نسخه فعال یافت نشد.")

        self.check_object_permissions(self.request, obj)
        return obj

    def get_queryset(self):
        queryset = super().get_queryset()
        revision_id = self.request.query_params.get('revision_id')
        user_id = self.request.query_params.get('user_id')  # <--- فیلتر جدید

        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)

        # فیلتر کردن تسک‌هایی که این کاربر در آن‌ها نقش دارد
        if user_id:
            queryset = queryset.filter(task__roles__user_id=user_id).distinct()

        return queryset

    # --- هندل کردن ساخت صحیح تسک (گرفتن والد از ریکوئست) ---
    def perform_create(self, serializer):
        revision_id = self.request.data.get('revision_id')
        print(self.request.data)
        print(revision_id)
        if not revision_id:
            raise ValidationError({"revision_id": "آیدی نسخه برای ساخت تسک الزامی است."})

        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision)

        # تسک باید حتما به یک WBS متصل شود
        parent_id = self.request.data.get('parentId')
        if not parent_id:
            raise ValidationError({"parentId": "مشخص کردن گره والد (WBS) برای ساخت تسک الزامی است."})

        wbs_node = get_object_or_404(WBSNodeVersion, node_id=parent_id, revision=revision)

        base_task = Task.objects.create(project=revision.project)
        serializer.save(task=base_task, revision=revision, wbs_node=wbs_node)

    def perform_update(self, serializer):
        check_revision_is_open(serializer.instance.revision)
        serializer.save()

    def perform_destroy(self, instance):
        check_revision_is_open(instance.revision)
        instance.is_deleted = True
        instance.save()


class DependencyViewSet(viewsets.ModelViewSet):
    queryset = Dependency.objects.all()
    serializer_class = DependencySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        return queryset

    def perform_create(self, serializer):
        # اضافه کردن چک باز بودن نسخه هنگام ایجاد یک Dependency
        revision_id = self.request.data.get('revisionId')
        if not revision_id:
            raise ValidationError({"revisionId": "آیدی نسخه الزامی است."})
        revision = get_object_or_404(Revision, id=revision_id)
        check_revision_is_open(revision)
        serializer.save(revision=revision)

    def perform_update(self, serializer):
        check_revision_is_open(serializer.instance.revision)
        serializer.save()

    def perform_destroy(self, instance):
        check_revision_is_open(instance.revision)
        instance.delete()  # وابستگی‌ها می‌توانند فیزیکی حذف شوند


class TaskReportLogViewSet(viewsets.ModelViewSet):
    queryset = TaskReportLog.objects.all()
    serializer_class = TaskReportLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        task_id = self.request.query_params.get('task_id')
        for_approval = self.request.query_params.get('for_approval')

        if task_id:
            queryset = queryset.filter(task_id=task_id)

        if for_approval == 'true':
            queryset = queryset.filter(
                task__roles__user=self.request.user,
                task__roles__role__in=['reviewer', 'project manager'],
                is_approved=False  # نمایش گزارش‌هایی که هنوز تایید نشده‌اند
            ).distinct()
        return queryset


    def perform_create(self, serializer):
        # فقط ثبت گزارش در حالت "در انتظار تایید"
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        report = serializer.instance
        # جلوگیری از ویرایش پس از تایید
        if report.is_approved:
            raise PermissionDenied("این گزارش قبلاً تایید شده و دیگر قابل ویرایش نیست.")
        serializer.save()

    @action(detail=True, methods=['post'], url_path='approve')
    def approve_report(self, request, pk=None):
        """تایید گزارش و اعمال پیشرفت روی تسک اصلی (گانت‌چارت)"""
        report = self.get_object()

        # ۱. بررسی اینکه آیا گزارش قبلاً تایید شده است یا خیر
        if report.is_approved:
            return Response({"detail": "این گزارش قبلاً تایید شده است."}, status=status.HTTP_400_BAD_REQUEST)

        # ۲. بررسی سطح دسترسی کاربر برای تایید این تسک خاص
        has_permission = TaskRole.objects.filter(
            task=report.task,
            user=request.user,
            role__in=['reviewer', 'project manager']
        ).exists()

        if not has_permission:
            raise PermissionDenied("شما دسترسی لازم (مدیر پروژه یا بررسی‌کننده) برای تایید گزارش این تسک را ندارید.")

        # ۳. تغییر وضعیت گزارش به تایید شده
        report.is_approved = True
        report.approved_by = request.user
        report.approved_at = timezone.now()
        report.save()

        # ۴. اعمال پیشرفت در TaskActual
        active_task_version = TaskVersion.objects.filter(
            task=report.task,
            revision__approved_at__isnull=True,  # نسخه‌ای که هنوز باز است
            is_deleted=False
        ).first()

        if active_task_version:
            task_actual, created = TaskActual.objects.get_or_create(
                task_version=active_task_version,
                defaults={'updated_by': request.user}
            )
            task_actual.progress = report.progress_percent
            task_actual.updated_by = request.user
            task_actual.save()

        return Response({"detail": "گزارش تایید شد و پیشرفت تسک به‌روزرسانی گردید."}, status=status.HTTP_200_OK)
class TaskChatMessageViewSet(viewsets.ModelViewSet):
    queryset = TaskChatMessage.objects.all()
    serializer_class = TaskChatMessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        task_id = self.request.query_params.get('task_id')
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class TaskRoleViewSet(viewsets.ModelViewSet):
    """مدیریت نقش‌های تخصیص داده شده به تسک‌ها (Task Roles)"""
    queryset = TaskRole.objects.all()
    serializer_class = TaskRoleSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()

        # امکان فیلتر کردن دیتای برگشتی
        revision_id = self.request.query_params.get('revision_id')
        task_id = self.request.query_params.get('taskId')
        user_id = self.request.query_params.get('userId')

        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        if task_id:
            queryset = queryset.filter(task_id=task_id)
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        return queryset


def _date_range(start: date, end: date):
    """Yield every date from start to end (inclusive)."""
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _bucket_key(d: date, granularity: str) -> str:
    if granularity == "week":
        return _week_key(d)
    if granularity == "month":
        return _month_key(d)
    return d.isoformat()          # "day" (default)


def _bucket_label(key: str, granularity: str) -> str:
    """Human-readable label for a bucket key."""
    if granularity == "day":
        d = date.fromisoformat(key)
        return d.strftime("%d %b")
    if granularity == "week":
        # key like "2026-W23"
        year, wk = key.split("-W")
        d = datetime.strptime(f"{year}-W{wk}-1", "%G-W%V-%u").date()
        return f"W{wk} ({d.strftime('%d %b')})"
    if granularity == "month":
        year, month = key.split("-")
        d = date(int(year), int(month), 1)
        return d.strftime("%b %Y")
    return key


def _working_days_in_bucket(bucket_dates: list[date]) -> int:
    """Count Mon–Fri days in a list of dates (simplistic; ignores CalendarExceptions)."""
    return sum(1 for d in bucket_dates if d.weekday() < 5)


# ─── view ─────────────────────────────────────────────────────────────────────

class ResourceHistogramView(APIView):
    """
    Returns a resource load histogram for a given revision.

    Response shape:
    {
      "revision_id": "...",
      "granularity": "day",
      "buckets": ["2026-06-01", "2026-06-02", ...],
      "bucket_labels": ["01 Jun", "02 Jun", ...],
      "resources": [
        {
          "id": 1,
          "name": "Ali Ahmadi",
          "capacity_hours_per_day": 8.0,
          "load": [
            {
              "bucket": "2026-06-01",
              "allocated_hours": 6.0,
              "capacity_hours": 8.0,
              "load_percent": 75.0,
              "status": "optimum",   // "underload" | "optimum" | "overload"
              "tasks": [
                {"task_id": "...", "title": "Design", "hours": 6.0}
              ]
            },
            ...
          ]
        },
        ...
      ]
    }
    """

    permission_classes = [IsAuthenticated]

    UNDERLOAD_THRESHOLD = 50    # % below this → underload
    OVERLOAD_THRESHOLD  = 100   # % above this → overload

    def get(self, request, revision_id):
        # ── 1. Fetch revision ──────────────────────────────────────────────
        try:
            revision = Revision.objects.get(pk=revision_id)
        except Revision.DoesNotExist:
            return Response({"detail": "Revision not found."}, status=status.HTTP_404_NOT_FOUND)

        granularity = request.query_params.get("granularity", "day")
        if granularity not in ("day", "week", "month"):
            return Response({"detail": "granularity must be day|week|month."}, status=status.HTTP_400_BAD_REQUEST)

        # ── 2. Pull all task versions for this revision ───────────────────
        task_versions = (
            TaskVersion.objects
            .filter(revision=revision, is_deleted=False)
            .exclude(planned_start=None)
            .exclude(planned_finish=None)
            .select_related("task")
        )

        # ── 3. Pull assignments for this revision ─────────────────────────
        assignments = (
            Assignment.objects
            .filter(revision=revision)
            .select_related("resource", "task")
        )

        # Map task_id → TaskVersion for quick lookup
        tv_by_task = {str(tv.task_id): tv for tv in task_versions}

        # ── 4. Determine global window ────────────────────────────────────
        starts  = [tv.planned_start.date() for tv in task_versions]
        finishes = [tv.planned_finish.date() for tv in task_versions]

        if not starts:
            return Response({
                "revision_id": str(revision_id),
                "granularity": granularity,
                "buckets": [],
                "bucket_labels": [],
                "resources": [],
            })

        window_start = date.fromisoformat(request.query_params["start"]) if "start" in request.query_params else min(starts)
        window_end   = date.fromisoformat(request.query_params["end"])   if "end"   in request.query_params else max(finishes)

        all_dates = list(_date_range(window_start, window_end))

        # ── 5. Build bucket → list[date] mapping ──────────────────────────
        bucket_dates: dict[str, list[date]] = defaultdict(list)
        for d in all_dates:
            bucket_dates[_bucket_key(d, granularity)].append(d)

        ordered_buckets = list(dict.fromkeys(_bucket_key(d, granularity) for d in all_dates))

        # ── 6. Build per-resource, per-bucket load ────────────────────────
        # Structure: resource_id → bucket_key → { allocated_hours, tasks }
        resource_load: dict[int, dict[str, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"allocated_hours": Decimal("0"), "tasks": []})
        )

        resources_seen: dict[int, Resource] = {}

        for asgn in assignments:
            tv = tv_by_task.get(str(asgn.task_id))
            if tv is None:
                continue

            resource = asgn.resource
            resources_seen[resource.id] = resource

            cap_per_day = resource.capacity_hours_per_day      # Decimal
            units_frac  = asgn.units_percent / Decimal("100")  # e.g. 0.5 for 50 %

            # Daily allocated hours from this assignment
            hours_per_working_day = cap_per_day * units_frac

            task_start  = tv.planned_start.date()
            task_finish = tv.planned_finish.date()

            # Clip to window
            eff_start = max(task_start,  window_start)
            eff_end   = min(task_finish, window_end)
            if eff_start > eff_end:
                continue

            for d in _date_range(eff_start, eff_end):
                if d.weekday() >= 5:        # skip weekends (simple rule)
                    continue
                bk = _bucket_key(d, granularity)
                resource_load[resource.id][bk]["allocated_hours"] += hours_per_working_day
                # Track which tasks contributed (deduplicate per bucket later)
                resource_load[resource.id][bk]["tasks"].append({
                    "task_id": str(asgn.task_id),
                    "title": tv.title,
                    "hours_per_day": float(round(hours_per_working_day, 2)),
                })

        # ── 7. Deduplicate task entries per bucket ────────────────────────
        for rid in resource_load:
            for bk in resource_load[rid]:
                seen_tasks: dict[str, float] = {}
                for t in resource_load[rid][bk]["tasks"]:
                    tid = t["task_id"]
                    seen_tasks[tid] = seen_tasks.get(tid, 0) + t["hours_per_day"]
                resource_load[rid][bk]["tasks"] = [
                    {"task_id": tid, "title": next(
                        t["title"] for t in resource_load[rid][bk]["tasks"] if t["task_id"] == tid
                    ), "hours": round(hrs, 2)}
                    for tid, hrs in seen_tasks.items()
                ]

        # ── 8. Assemble response ──────────────────────────────────────────
        result_resources = []

        # Also include resources that have NO assignments (capacity still useful)
        all_resources = Resource.objects.filter(project=revision.project)
        for res in all_resources:
            resources_seen.setdefault(res.id, res)

        for res in resources_seen.values():
            cap_per_day = float(res.capacity_hours_per_day)
            load_buckets = []

            for bk in ordered_buckets:
                working_days = _working_days_in_bucket(bucket_dates[bk])
                bucket_capacity = cap_per_day * working_days

                allocated = float(resource_load[res.id][bk]["allocated_hours"])
                load_pct  = (allocated / bucket_capacity * 100) if bucket_capacity > 0 else 0.0

                if load_pct <= 0:
                    st = "idle"
                elif load_pct < self.UNDERLOAD_THRESHOLD:
                    st = "underload"
                elif load_pct <= self.OVERLOAD_THRESHOLD:
                    st = "optimum"
                else:
                    st = "overload"

                load_buckets.append({
                    "bucket":           bk,
                    "allocated_hours":  round(allocated, 2),
                    "capacity_hours":   round(bucket_capacity, 2),
                    "load_percent":     round(load_pct, 1),
                    "status":           st,
                    "tasks":            resource_load[res.id][bk]["tasks"],
                })

            result_resources.append({
                "id":                    res.id,
                "name":                  res.name,
                "capacity_hours_per_day": cap_per_day,
                "user_id":               res.user_id,
                "load":                  load_buckets,
            })

        # Sort resources: most overloaded first
        result_resources.sort(
            key=lambda r: -max((b["load_percent"] for b in r["load"]), default=0)
        )

        return Response({
            "revision_id":    str(revision_id),
            "granularity":    granularity,
            "buckets":        ordered_buckets,
            "bucket_labels":  [_bucket_label(b, granularity) for b in ordered_buckets],
            "resources":      result_resources,
        })


class ImportMSPView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        xml_file = request.FILES.get("file")
        # دریافت project_id و revision_id از درخواست
        project_id = request.data.get("project_id")
        revision_id = request.data.get("revision_id")
        active_node_id = request.data.get('active_node_id')
        if not xml_file:
            return Response({"error": "No file provided."}, status=400)

        if not project_id or not revision_id:
            return Response({"error": "project_id and revision_id are required."}, status=400)

        if not xml_file.name.lower().endswith(".xml"):
            return Response({"error": "File must be a .xml export from MS Project."}, status=400)

        try:
            # فراخوانی تابع اصلاح شده در msp_importer.py
            result = import_msp_xml(xml_file, project_id, revision_id, active_node_id=active_node_id)
        except Exception as exc:
            return Response(
                {"error": "Import failed.", "detail": str(exc)},
                status=500,
            )

        return Response(result, status=200)
class ResourcePoolViewSet(viewsets.ModelViewSet):
    queryset = ResourcePool.objects.all()
    serializer_class = ResourcePoolSerializer
    permission_classes = [IsAuthenticated]

class ResourceRoleViewSet(viewsets.ModelViewSet):
    queryset = ResourceRole.objects.all()
    serializer_class = ResourceRoleSerializer
    permission_classes = [IsAuthenticated]

class ResourceSkillViewSet(viewsets.ModelViewSet):
    queryset = ResourceSkill.objects.all()
    serializer_class = ResourceSkillSerializer
    permission_classes = [IsAuthenticated]

class ResourceViewSet(viewsets.ModelViewSet):
    queryset = Resource.objects.all()
    serializer_class = ResourceSerializer
    permission_classes = [IsAuthenticated]

class ResourceSkillMappingViewSet(viewsets.ModelViewSet):
    queryset = ResourceSkillMapping.objects.all()
    serializer_class = ResourceSkillMappingSerializer
    permission_classes = [IsAuthenticated]

class ResourceExceptionViewSet(viewsets.ModelViewSet):
    queryset = ResourceException.objects.all()
    serializer_class = ResourceExceptionSerializer
    permission_classes = [IsAuthenticated]

class ResourceRateViewSet(viewsets.ModelViewSet):
    queryset = ResourceRate.objects.all()
    serializer_class = ResourceRateSerializer
    permission_classes = [IsAuthenticated]

class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        فیلتر کردن Assignment ها بر اساس ریویژن (Revision)
        این متد باعث می‌شود وقتی فرانت‌اند `?revision_id=xxx` را می‌فرستد،
        فقط رکوردهای همان نسخه برگشت داده شود.
        """
        queryset = super().get_queryset()
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        return queryset


from django.contrib.auth import get_user_model
User = get_user_model()
class PersonalTaskViewSet(viewsets.ViewSet):
    """
    مدیریت تسک‌های شخصی کاربران که به عنوان یک پروژه سیستمی در بک‌اند ثبت می‌شوند.
    """
    permission_classes = [IsAuthenticated]

    # متد GET برای گرفتن لیست تسک‌های شخصی از سمت فرانت‌اند
    def list(self, request):
        sys_project = Project.objects.filter(name="System-Personal-Tasks").first()
        if not sys_project:
            # اگر پروژه هنوز ساخته نشده، یعنی کاربر هنوز تسکی ایجاد نکرده است
            return Response([], status=status.HTTP_200_OK)

        # پیدا کردن ریویژن فعال و تمام تسک‌هایی که حذف نشده‌اند
        revision = Revision.objects.filter(project=sys_project).latest('created_at')
        tasks = TaskVersion.objects.filter(revision=revision, is_deleted=False)

        # استفاده از سریالایزر گانت‌چارت برای همخوانی ساختار دیتا با فرانت‌اند
        serializer = ActivityNodeSerializer(tasks, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # متد POST برای ایجاد تسک شخصی جدید
    @action(detail=False, methods=['post'], url_path='create')
    @transaction.atomic
    def create_personal_task(self, request):
        title = request.data.get('title')
        start_date = request.data.get('start_date')
        duration_hours = request.data.get('duration_hours')
        description = request.data.get('description')
        user_id = request.data.get('user_id')
        current_id = (request.data.get('current_user')).get('id')

        if not all([title, start_date, duration_hours, user_id]):
            return Response({"detail": "تمامی فیلدها (عنوان، تاریخ، مدت‌زمان و کاربر) الزامی است."},
                            status=status.HTTP_400_BAD_REQUEST)

        # ۱. ساخت یا دریافت پروژه سیستمی
        sys_project, created = Project.objects.get_or_create(
            name="System-Personal-Tasks",
            defaults={'created_by': request.user}
        )

        # ۲. دریافت ریویژن (طبق مدل‌های شما، ریویژن صفر خودکار با ساخت پروژه ایجاد می‌شود)
        revision = Revision.objects.filter(project=sys_project).latest('created_at')

        # ۳. مدیریت ساختار WBS برای تسک‌های شخصی
        # مدل WBSNode فیلد نام ندارد، نام در WBSNodeVersion ذخیره می‌شود
        wbs_node_version = WBSNodeVersion.objects.filter(revision=revision, title="My Personal Tasks").first()

        if not wbs_node_version:
            # پیدا کردن گره ریشه که با سیگنال ایجاد شده
            root_wbs = WBSNodeVersion.objects.get(revision=revision, parent__isnull=True)

            # ساخت گره WBS فرزند برای کارهای شخصی
            base_node = WBSNode.objects.create(project=sys_project)
            wbs_node_version = WBSNodeVersion.objects.create(
                node=base_node,
                revision=revision,
                parent=root_wbs,
                title="My Personal Tasks",
                sequence=1
            )

        # ۴. ساخت تسک فیزیکی و نسخه آن
        task = Task.objects.create(project=sys_project)

        task_ver = TaskVersion.objects.create(
            task=task,
            revision=revision,
            wbs_node=wbs_node_version,
            title=title,
            planned_start=start_date,
            duration_hours=duration_hours,
            description=description,
        )

        # ۵. ایجاد نقش مجری
        # این کار باعث می‌شود سیگنالی که در signals.py دارید، فوراً کاربر را به جدول Assignment
        # اضافه کند تا برای لولینگ آماده شود.
        user = User.objects.get(id=user_id)
        current=User.objects.get(id=current_id)
        TaskRole.objects.create(
            revision=revision,
            task=task,
            user=user,
            role='executor'
        )
        TaskRole.objects.create(
            revision=revision,
            task=task,
            user=current,
            role='reviewer'
        )


        # ۶. بازگرداندن دیتای تسک با فرمت استاندارد برای نمایش سریع در لیست فرانت‌اند
        serializer = ActivityNodeSerializer(task_ver)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # متد DELETE برای لغو یا پاک کردن تسک شخصی
    def destroy(self, request, pk=None):
        try:
            task_ver = TaskVersion.objects.get(task__id=pk)

            # استفاده از ویژگی Soft Delete که در سیستم شما پیاده‌سازی شده است
            task_ver.is_deleted = True
            task_ver.save()

            # حذف نقش کاربر تا سیگنال remove_executor_assignment در signals.py
            # تریگر شود و منبع را از Assignment پاک کند، تا ظرفیت آزاد شود.
            TaskRole.objects.filter(task__id=pk).delete()

            return Response(status=status.HTTP_204_NO_CONTENT)
        except TaskVersion.DoesNotExist:
            return Response({"detail": "تسک یافت نشد."}, status=status.HTTP_404_NOT_FOUND)


    def partial_update(self, request, pk=None):
        try:
            # پیدا کردن تسک فعلی که حذف نشده باشد
            task_ver = TaskVersion.objects.get(task__id=pk, is_deleted=False)

            # دریافت فیلدهای ارسال شده از سمت کلاینت
            title = request.data.get('title')
            start_date = request.data.get('start_date')
            duration_hours = request.data.get('duration_hours')
            description = request.data.get('description')
            user_id = request.data.get('user_id')

            # اعمال تغییرات روی تسک (در صورت وجود هر فیلد در ریکوئست)
            if title:
                task_ver.title = title
            if start_date:
                task_ver.planned_start = start_date
            if duration_hours:
                task_ver.duration_hours = duration_hours
            if description is not None:  # توضیحات می‌تواند خالی باشد
                task_ver.description = description

            task_ver.save()

            # در صورتی که کاربر مجری تغییر کرده باشد، نقش او را آپدیت می‌کنیم
            if user_id:
                task_role = TaskRole.objects.filter(task=task_ver.task, role='executor').first()
                if task_role:
                    if str(task_role.user_id) != str(user_id):
                        task_role.user_id = user_id
                        task_role.save()
                else:
                    # اگر نقشی از قبل نبود، یکی می‌سازیم
                    TaskRole.objects.create(
                        revision=task_ver.revision,
                        task=task_ver.task,
                        user_id=user_id,
                        role='executor'
                    )

            # استفاده از همان سریالایزری که در لیست و ساخت استفاده کردید
            serializer = ActivityNodeSerializer(task_ver)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except TaskVersion.DoesNotExist:
            return Response({"detail": "تسک یافت نشد."}, status=status.HTTP_404_NOT_FOUND)


class VarianceReportViewSet(viewsets.ModelViewSet):
    """مدیریت گزارش‌های انحراف و اتصال به موتور EVM"""
    queryset = VarianceReport.objects.all()
    serializer_class = VarianceReportSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        revision_id = self.request.query_params.get('revision_id')
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
        return queryset

    @action(detail=False, methods=['post'], url_path='calculate')
    def trigger_calculation(self, request):
        """اجرای دستی موتور محاسباتی برای یک پروژه"""
        project_id = request.data.get('project_id')
        if not project_id:
            return Response({"error": "project_id الزامی است."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # اجرای انجین
            engine = EVMEngine(project_id=project_id)
            engine.run_task_level_variances()
            return Response({"status": "محاسبات با موفقیت انجام شد و دیتابیس به‌روزرسانی گردید."},
                            status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)