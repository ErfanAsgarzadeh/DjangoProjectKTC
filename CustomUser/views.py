from rest_framework import generics, status
from rest_framework.decorators import api_view
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

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

class UserListView(generics.ListAPIView):
    """
    دریافت لیست تمام کاربران سیستم برای تخصیص به تسک‌ها در فرانت‌اند
    """
    queryset = CustomUser.objects.all().order_by('id')
    serializer_class = CustomUserSerializer
    permission_classes = [IsAuthenticated]


@api_view(['POST'])
def logout_view(request):
    try:
        refresh_token = request.data.get("refresh")

        if not refresh_token:
            return Response(
                {"detail": "Refresh token required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        token = RefreshToken(refresh_token)
        token.blacklist()

        return Response({"detail": "Logged out successfully"})

    except Exception:
        return Response({"detail": "Invalid token"}, status=status.HTTP_400_BAD_REQUEST)