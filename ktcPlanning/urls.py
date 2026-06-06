from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProjectViewSet,
    RevisionViewSet,
    WbsNodeViewSet,
    ActivityNodeViewSet,
    DependencyViewSet, TaskReportLogViewSet, TaskChatMessageViewSet, TaskRoleViewSet, ResourceHistogramView
)

# ایجاد یک نمونه از روتور پیش‌فرض DRF
router = DefaultRouter()

# ثبت ویوها در روتور
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'revisions', RevisionViewSet, basename='revision')
router.register(r'wbs-nodes', WbsNodeViewSet, basename='wbs-node')
router.register(r'activities', ActivityNodeViewSet, basename='activity')
router.register(r'dependencies', DependencyViewSet, basename='dependency')
router.register(r'task-reports', TaskReportLogViewSet, basename='task-report')
router.register(r'task-chats', TaskChatMessageViewSet, basename='task-chat')
router.register(r'task-roles', TaskRoleViewSet, basename='task-role') # ثبت مسیر نقش‌ها
# مسیرهای نهایی اپلیکیشن
urlpatterns = [
    path('', include(router.urls)),
    path('planning/revisions/<uuid:revision_id>/resource-histogram/',
         ResourceHistogramView.as_view(), name='resource-histogram'),
]