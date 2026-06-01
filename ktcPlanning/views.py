from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db import transaction

from .cpm import run_cpm
# ایمپورت تمامی مدل‌های مورد نیاز
from .models import Project, Revision, WBSNodeVersion, TaskVersion, Dependency, TaskRole, Task, WBSNode
from .serializers import (
    ProjectSerializer,
    RevisionSerializer,
    WbsNodeSerializer,
    ActivityNodeSerializer,
    DependencySerializer,
    TaskRoleSerializer
)


def check_revision_is_open(revision):
    if revision.approved_at is not None:
        raise PermissionDenied("این نسخه قفل شده است و قابل تغییر نیست.")


class ProjectViewSet(viewsets.ModelViewSet):
    """مدیریت پروژه‌ها"""
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class RevisionViewSet(viewsets.ModelViewSet):
    """مدیریت نسخه‌ها (Revisions) با قابلیت فیلتر بر اساس پروژه"""
    queryset = Revision.objects.all().order_by('-number')
    serializer_class = RevisionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        project_id = self.request.query_params.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

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

        tasks = TaskVersion.objects.filter(revision=revision, is_deleted=False)
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

        new_revision_number = Revision.objects.filter(project=base_revision.project).count() + 1
        new_revision = Revision.objects.create(
            project=base_revision.project,
            number=new_revision_number,
            description=f"پیش‌نویس ساخته شده از روی نسخه {base_revision.number}",
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
                    duration_hours=old_task.duration_hours
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
            cpm_result = run_cpm(revision)

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
            parent_node = get_object_or_404(WBSNodeVersion, id=parent_id, revision=revision)

        base_node = WBSNode.objects.create(project=revision.project)
        serializer.save(node=base_node, revision=revision, parent=parent_node)

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
    queryset = TaskVersion.objects.filter(is_deleted=False)
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
        if revision_id:
            queryset = queryset.filter(revision_id=revision_id)
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

        wbs_node = get_object_or_404(WBSNodeVersion, id=parent_id, revision=revision)

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


