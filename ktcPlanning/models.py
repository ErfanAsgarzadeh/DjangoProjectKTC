import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from mptt.models import MPTTModel
from mptt.fields import TreeForeignKey

User = get_user_model()


# =========================================================
# 1. PROJECT
# =========================================================

class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


# =========================================================
# 2. CALENDAR SYSTEM
# =========================================================

class Calendar(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="calendars")
    name = models.CharField(max_length=255)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.name} ({self.project.name})"


class WorkingInterval(models.Model):
    WEEKDAYS = [
        (0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"),
        (4, "Fri"), (5, "Sat"), (6, "Sun"),
    ]

    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="intervals")
    weekday = models.IntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()

    def clean(self):
        if self.start_time >= self.end_time:
            raise ValidationError("Start time must be before end time.")


class CalendarException(models.Model):
    calendar = models.ForeignKey(Calendar, on_delete=models.CASCADE, related_name="exceptions")
    date = models.DateField()
    is_working = models.BooleanField(default=False)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("calendar", "date")]


# =========================================================
# 3. REVISION ENGINE (CORE OF REPLANNING)
# =========================================================

class Revision(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    description = models.TextField(blank=True)
    is_baseline = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="created_revisions"
    )
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.PROTECT, related_name="approved_revisions"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    project_start=models.DateTimeField()
    class Meta:
        unique_together = [("project", "number")]

    def __str__(self):
        return f"Rev {self.number} - {self.project.name}"


# =========================================================
# 4. WBS (TREE STRUCTURE)
# =========================================================

class WBSNode(models.Model):
    id=models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="wbs")
    created_at = models.DateTimeField(auto_now_add=True)



# immutable revisioned layer

class WBSNodeVersion(MPTTModel):

    node = models.ForeignKey(WBSNode,on_delete=models.CASCADE,related_name="versions")
    revision = models.ForeignKey(Revision,on_delete=models.CASCADE,related_name="wbs_versions")
    parent = TreeForeignKey("self",null=True,blank=True, on_delete=models.CASCADE,related_name="children")
    title = models.CharField(max_length=255)
    sequence = models.PositiveIntegerField(default=1)
    is_deleted = models.BooleanField(default=False)

    class MPTTMeta:
        order_insertion_by = ["sequence"]

    class Meta:
        unique_together = [
            ("node", "revision"),
            ("revision", "parent", "sequence")
        ]

    @property
    def wbs_code(self):
        ancestors = self.get_ancestors(include_self=True)
        return ".".join(str(a.sequence) for a in ancestors)
# =========================================================
# 5. TASK (IDENTITY ONLY)
# =========================================================

class Task(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.id)


# =========================================================
# 6. TASK VERSION (IMMUTABLE SCHEDULE STATE)
# =========================================================

class TaskVersion(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="versions")
    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="task_versions")
    wbs_node = models.ForeignKey(WBSNodeVersion, on_delete=models.PROTECT)
    title = models.CharField(max_length=255)
    calendar = models.ForeignKey(Calendar, null=True, blank=True, on_delete=models.SET_NULL)

    planned_start = models.DateTimeField(null=True, blank=True)
    planned_finish = models.DateTimeField(null=True, blank=True)
    duration_hours = models.DecimalField(max_digits=10, decimal_places=2)

    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [("task", "revision")]
        indexes = [
            models.Index(fields=["revision", "wbs_node"]),
            models.Index(fields=["planned_start"]),
        ]

    def clean(self):
        if  self.planned_start and self.planned_finish:
            if self.planned_start >= self.planned_finish:
                raise ValidationError("Start must be before finish.")


# =========================================================
# 7. DEPENDENCIES (VERSIONED CPM GRAPH)
# =========================================================

class Dependency(models.Model):
    LINK_TYPES = [
        ("FS", "Finish-Start"),
        ("SS", "Start-Start"),
        ("FF", "Finish-Finish"),
        ("SF", "Start-Finish"),
    ]

    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="dependencies")
    predecessor = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="outgoing")
    successor = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="incoming")
    dependency_type = models.CharField(max_length=2, choices=LINK_TYPES, default="FS")
    lag_hours = models.IntegerField(default=0)

    class Meta:
        unique_together = [("revision", "predecessor", "successor")]

    def clean(self):
        if self.predecessor_id == self.successor_id:
            raise ValidationError("Self dependency not allowed.")


# =========================================================
# 8. CPM METRICS CACHE (COMPUTED LAYER)
# =========================================================

class TaskScheduleMetrics(models.Model):
    task_version = models.OneToOneField(
        TaskVersion, on_delete=models.CASCADE, related_name="metrics"
    )
    early_start = models.DateTimeField()
    early_finish = models.DateTimeField()
    late_start = models.DateTimeField()
    late_finish = models.DateTimeField()
    total_float_hours = models.IntegerField(default=0)
    free_float_hours = models.IntegerField(default=0)
    is_critical = models.BooleanField(default=False)


# =========================================================
# 9. RESOURCE MODEL
#    - Resource به User وصل شد (اختیاری — می‌تواند منبع غیرانسانی هم باشد)
# =========================================================

class Resource(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="resources")
    name = models.CharField(max_length=255)
    # اگر منبع یک کاربر سیستم است، اینجا لینک می‌شود
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="resources"
    )
    capacity_hours_per_day = models.DecimalField(max_digits=5, decimal_places=2, default=8)

    def __str__(self):
        return self.name


class Assignment(models.Model):
    """
    اختصاص منابع به تسک در یک revision مشخص.
    عمداً روی Task است نه TaskVersion تا بتوان در resource leveling
    از آن استفاده کرد — اما با revision نسخه‌بندی می‌شود.
    """
    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="assignments")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="assignments")
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE)
    units_percent = models.DecimalField(max_digits=5, decimal_places=2, default=100)

    class Meta:
        unique_together = [("revision", "task", "resource")]


# =========================================================
# 10. TASK ROLE — نقش افراد روی تسک (جدید)
#     هم نسخه‌بندی‌شده (per revision) و هم به User وصل است
# =========================================================

class TaskRole(models.Model):
    ROLES = [
        ("owner",    "مسئول اصلی"),
        ("reviewer", "بررسی‌کننده"),
        ("executor", "مجری"),
        ("project manager", "مدیر پروژه"),
    ]

    revision = models.ForeignKey(Revision, on_delete=models.CASCADE, related_name="task_roles")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="roles")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="task_roles")
    role = models.CharField(max_length=20, choices=ROLES)

    class Meta:
        unique_together = [("revision", "task", "user", "role")]

    def __str__(self):
        return f"{self.user} — {self.role} on {self.task}"


# =========================================================
# 11. TASK ACTUAL — واقعیت اجرا (جدید)
#     progress از TaskVersion به اینجا منتقل شد
# =========================================================

class TaskActual(models.Model):
    task_version = models.OneToOneField(
        TaskVersion, on_delete=models.CASCADE, related_name="actual"
    )
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_finish = models.DateTimeField(null=True, blank=True)
    # درصد پیشرفت واقعی — جای progress در TaskVersion
    progress = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    updated_by = models.ForeignKey(User, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.actual_start and self.actual_finish:
            if self.actual_start >= self.actual_finish:
                raise ValidationError("actual_start باید قبل از actual_finish باشد.")


# =========================================================
# 12. VARIANCE REPORT — انحراف از برنامه (جدید)
#     توسط Celery شبانه یا on-demand محاسبه می‌شود
# =========================================================

class VarianceReport(models.Model):
    STATUS = [
        ("on_track", "در برنامه"),
        ("at_risk",  "در خطر"),
        ("delayed",  "تاخیر دارد"),
    ]

    task_version = models.OneToOneField(
        TaskVersion, on_delete=models.CASCADE, related_name="variance"
    )
    start_variance_hours = models.IntegerField(default=0)    # مثبت = دیرتر از برنامه
    finish_variance_hours = models.IntegerField(default=0)   # مثبت = دیرتر از برنامه
    duration_variance_hours = models.IntegerField(default=0)
    spi = models.DecimalField(                               # Schedule Performance Index
        max_digits=5, decimal_places=2, default=1
    )
    status = models.CharField(max_length=10, choices=STATUS, default="on_track")
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status"]),  # برای query سریع روی داشبورد
        ]


# =========================================================
# 13. BASELINE (REVISION-BASED SNAPSHOT)
# =========================================================

class Baseline(models.Model):
    revision = models.OneToOneField(Revision, on_delete=models.CASCADE, related_name="baseline")
    created_at = models.DateTimeField(auto_now_add=True)

