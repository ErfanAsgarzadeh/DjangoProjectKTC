# ktcPlanning/serializers.py
from rest_framework import serializers

from .models import *


class ProjectSerializer(serializers.ModelSerializer):
    createdAt = serializers.DateTimeField(source='created_at', format="%Y-%m-%dT%H:%M:%S", read_only=True)
    description = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ['id', 'name', 'description', 'createdAt']

    def get_description(self, obj):
        return ""


class RevisionSerializer(serializers.ModelSerializer):
    projectId = serializers.PrimaryKeyRelatedField(source='project', read_only=True)
    projectStart = serializers.DateTimeField(source='project_start', format="%Y-%m-%d")
    createdAt = serializers.DateTimeField(source='created_at', format="%Y-%m-%dT%H:%M:%S")
    approvedAt = serializers.DateTimeField(source='approved_at', format="%Y-%m-%dT%H:%M:%S")
    isBaseline = serializers.BooleanField(source='is_baseline')

    class Meta:
        model = Revision
        fields = ['id', 'projectId', 'number', 'description', 'projectStart', 'createdAt','approvedAt', 'isBaseline']


class WbsNodeSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source='wbs_code', read_only=True)
    name = serializers.CharField(source='title')
    parentId = serializers.PrimaryKeyRelatedField(source='parent', read_only=True)
    type = serializers.SerializerMethodField()
    isExpanded = serializers.SerializerMethodField()

    startDate = serializers.SerializerMethodField()
    endDate = serializers.SerializerMethodField()
    duration = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    class Meta:
        model = WBSNodeVersion
        fields = ['id', 'code', 'name', 'parentId', 'type', 'isExpanded',
                  'startDate', 'endDate', 'duration', 'progress']

    def get_type(self, obj):
        return 'wbs'

    def get_isExpanded(self, obj):
        return True

    def get_startDate(self, obj):
        return None

    def get_endDate(self, obj):
        return None

    def get_duration(self, obj):
        return 0

    def get_progress(self, obj):
        return 0


class ActivityNodeSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source='task.id', read_only=True)
    code = serializers.SerializerMethodField()
    name = serializers.CharField(source='title')
    parentId = serializers.PrimaryKeyRelatedField(source='wbs_node', read_only=True)
    type = serializers.SerializerMethodField()

    startDate = serializers.DateTimeField(source='planned_start', format="%Y-%m-%d", allow_null=True)
    endDate = serializers.DateTimeField(source='planned_finish', format="%Y-%m-%d", allow_null=True)

    # تغییر مهم: فیلد duration حالا می‌تواند از فرانت‌اند دریافت شود
    duration = serializers.FloatField(required=False, write_only=True)

    progress = serializers.SerializerMethodField()
    resources = serializers.SerializerMethodField()
    constraintType = serializers.SerializerMethodField()
    constraintDate = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()

    class Meta:
        model = TaskVersion
        fields = [
            'id', 'code', 'name', 'parentId', 'type', 'startDate', 'endDate',
            'duration', 'progress', 'resources', 'constraintType', 'constraintDate', 'notes'
        ]

    # --- تبدیل دیتا هنگام ارسال به فرانت‌اند (ساعت به روز) ---
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['duration'] = float(instance.duration_hours) / 8.0 if instance.duration_hours else 0
        return data

    # --- تبدیل دیتا هنگام دریافت از فرانت‌اند (روز به ساعت) ---
    def validate(self, attrs):
        if 'duration' in attrs:
            attrs['duration_hours'] = attrs.pop('duration') * 8.0
        elif not self.instance:  # اگر ساخت تسک جدید بود و مقداری نیامد
            attrs['duration_hours'] = 40.0  # دیفالت 5 روز
        return attrs

    def get_type(self, obj):
        return 'activity'

    def get_code(self, obj):
        return f"ACT-{str(obj.task.id)[:4].upper()}"

    def get_progress(self, obj):
        if hasattr(obj, 'actual') and obj.actual:
            return float(obj.actual.progress)
        return 0

    def get_resources(self, obj):
        assignments = Assignment.objects.filter(task=obj.task, revision=obj.revision)
        return [assign.resource.name for assign in assignments]

    def get_constraintType(self, obj):
        return "ASAP"

    def get_constraintDate(self, obj):
        return None

    def get_notes(self, obj):
        return ""


class DependencySerializer(serializers.ModelSerializer):
    fromId = serializers.UUIDField(source='predecessor_id')
    toId = serializers.UUIDField(source='successor_id')
    type = serializers.CharField(source='dependency_type')
    lag = serializers.SerializerMethodField()

    class Meta:
        model = Dependency
        fields = ['id', 'fromId', 'toId', 'type', 'lag']

    def create(self, validated_data):
        predecessor_id = validated_data.pop('predecessor_id')
        successor_id = validated_data.pop('successor_id')

        validated_data['predecessor_id'] = predecessor_id
        validated_data['successor_id'] = successor_id

        return super().create(validated_data)

    def get_lag(self, obj):
        return obj.lag_hours / 8 if obj.lag_hours else 0


class TaskRoleSerializer(serializers.ModelSerializer):
    revisionId = serializers.PrimaryKeyRelatedField(source='revision', read_only=True)
    taskId = serializers.PrimaryKeyRelatedField(source='task', read_only=True)
    userId = serializers.PrimaryKeyRelatedField(source='user', read_only=True)

    class Meta:
        model = TaskRole
        fields = ['id', 'revisionId', 'taskId', 'userId', 'role']