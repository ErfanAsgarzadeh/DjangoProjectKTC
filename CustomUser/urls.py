from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    RegisterView, UserProfileView, UserListView, logout_view,
    OrgUnitViewSet, UserManagementViewSet
)

router = DefaultRouter()
router.register(r'org-units', OrgUnitViewSet, basename='org-unit')
router.register(r'manage-users', UserManagementViewSet, basename='manage-user')

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('users/', UserListView.as_view(), name='user-list'),
    path('logout/', logout_view, name='logout'),
    path('', include(router.urls)),
]
