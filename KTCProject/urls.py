from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),

    # ۱. مسیرهای دریافت و تمدید توکن احراز هویت (JWT)
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # ۲. متصل کردن مسیرهای بخش برنامه‌ریزی و گانت چارت
    path('api/planning/', include('ktcPlanning.urls')),
    path('api/auth/', include('CustomUser.urls'))
]