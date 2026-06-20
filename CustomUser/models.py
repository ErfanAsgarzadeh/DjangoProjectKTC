from django.db import models
from django.contrib.auth.models import AbstractUser


class OrgUnit(models.Model):
    """واحد سازمانی (دپارتمان)"""
    name = models.CharField(max_length=255, verbose_name="نام واحد")
    description = models.CharField(max_length=255, blank=True, default='')
    manager = models.ForeignKey(
        'CustomUser', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='managed_units',
        verbose_name="مدیر واحد"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class CustomUser(AbstractUser):
    ORG_ROLES = [
        ("company_admin", "مدیر سیستم"),
        ("company_pm", "مدیر پروژه شرکت"),
        ("unit_manager", "مدیر واحد"),
        ("member", "عضو"),
    ]

    employee_code = models.CharField(max_length=10, unique=True, null=True, blank=True, verbose_name="Employee Code")
    job_title = models.CharField(max_length=100, null=True, blank=True, verbose_name="Job Title")

    # ساختار سازمانی
    unit = models.ForeignKey(
        OrgUnit, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='members',
        verbose_name="واحد سازمانی"
    )
    org_role = models.CharField(max_length=20, choices=ORG_ROLES, default="member", verbose_name="نقش سازمانی")

    def __str__(self):
        return f"{self.username}({self.job_title if self.job_title else self.username})"
