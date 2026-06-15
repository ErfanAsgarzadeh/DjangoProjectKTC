
from django.urls import path
from .views import RegisterView, UserProfileView, UserListView, logout_view

urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth_register'),
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('users/', UserListView.as_view(), name='user-list'),
    path('logout/', logout_view, name='logout'),
]