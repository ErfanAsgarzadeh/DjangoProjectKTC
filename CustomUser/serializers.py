from rest_framework import serializers
from .models import CustomUser, OrgUnit


class OrgUnitSerializer(serializers.ModelSerializer):
    id = serializers.CharField(read_only=True)
    managerId = serializers.PrimaryKeyRelatedField(
        source='manager', queryset=CustomUser.objects.all(),
        required=False, allow_null=True
    )
    managerName = serializers.CharField(source='manager.username', read_only=True, default=None)
    membersCount = serializers.SerializerMethodField()

    class Meta:
        model = OrgUnit
        fields = ['id', 'name', 'description', 'managerId', 'managerName', 'membersCount']

    def get_membersCount(self, obj):
        return obj.members.count()


class CustomUserSerializer(serializers.ModelSerializer):
    # تبدیل خودکار id عددی جنگو به string برای تطبیق کامل با types.ts
    id = serializers.CharField(read_only=True)

    # مپ کردن فیلدهای مدل ساخته شده شما به کلیدهای فرانت‌اند
    jobTitle = serializers.CharField(source='job_title', required=False, allow_blank=True, allow_null=True)
    employeeCode = serializers.CharField(source='employee_code', required=False, allow_blank=True, allow_null=True)

    # ساختار سازمانی
    unitId = serializers.PrimaryKeyRelatedField(
        source='unit', queryset=OrgUnit.objects.all(),
        required=False, allow_null=True
    )
    unitName = serializers.CharField(source='unit.name', read_only=True, default=None)
    orgRole = serializers.CharField(source='org_role', required=False)

    class Meta:
        model = CustomUser
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'jobTitle', 'employeeCode', 'password',
            'unitId', 'unitName', 'orgRole'
        ]

        # پسورد در پاسخ‌های API بازگردانده نمی‌شود (فقط برای دریافت و هش کردن است)
        extra_kwargs = {
            'password': {'write_only': True, 'required': False}
        }

    def create(self, validated_data):
        # ساخت کاربر با این متد برای هش شدن اصولی رمز عبور الزامی است
        user = CustomUser.objects.create_user(**validated_data)
        return user

    def update(self, instance, validated_data):
        # رمز عبور باید جداگانه و با هش صحیح ذخیره شود
        password = validated_data.pop('password', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if password:
            instance.set_password(password)

        instance.save()
        return instance
