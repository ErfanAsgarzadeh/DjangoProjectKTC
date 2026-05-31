from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProjectViewSet,
    RevisionViewSet,
    WbsNodeViewSet,
    ActivityNodeViewSet,
    DependencyViewSet
)

# ایجاد یک نمونه از روتور پیش‌فرض DRF
router = DefaultRouter()

# ثبت ویوها در روتور
router.register(r'projects', ProjectViewSet, basename='project')
router.register(r'revisions', RevisionViewSet, basename='revision')
router.register(r'wbs-nodes', WbsNodeViewSet, basename='wbs-node')
router.register(r'activities', ActivityNodeViewSet, basename='activity')
router.register(r'dependencies', DependencyViewSet, basename='dependency')

# مسیرهای نهایی اپلیکیشن
urlpatterns = [
    path('', include(router.urls)),
]