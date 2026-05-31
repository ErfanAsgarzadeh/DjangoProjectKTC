from rest_framework import generics
from rest_framework.permissions import AllowAny, IsAuthenticated
from .models import CustomUser
from .serializers import CustomUserSerializer

class RegisterView(generics.CreateAPIView):
    """
    ثبت‌نام کاربر جدید
    """
    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = [AllowAny] # اجازه دسترسی به همه برای ثبت‌نام


class UserProfileView(generics.RetrieveUpdateAPIView):
    """
    مشاهده و ویرایش پروفایل کاربر جاری (بر اساس توکن JWT)
    """
    serializer_class = CustomUserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        # این متد به صورت خودکار کاربری که توکن آن ارسال شده را برمی‌گرداند
        # نیازی نیست فرانت‌اند ID کاربر را در URL بفرستد
        return self.request.user