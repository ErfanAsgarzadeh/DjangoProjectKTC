"""
منطق مرکزی مجوزدهی (Authorization) بر اساس نقش سازمانی و واحد.

قوانین اصلی:
- مشاهده (read): برای همه‌ی کاربران احراز هویت‌شده آزاد است.
- ساخت پروژه: company_admin / company_pm (کل شرکت) و unit_manager (واحد خودش).
- ویرایش پروژه: مدیر شرکت، سازنده‌ی پروژه، یا مدیرِ واحدِ صاحب پروژه.

نکته‌ی امنیتی: superuser همیشه دسترسی کامل دارد (safety net تا کسی قفل نشود).
"""

from rest_framework.exceptions import PermissionDenied


def _role(user):
    return getattr(user, 'org_role', 'member') or 'member'


def is_company_level(user) -> bool:
    """مدیر سیستم یا مدیر پروژه‌های شرکت (یا superuser)."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or _role(user) in ('company_admin', 'company_pm')


def can_create_project(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or _role(user) in ('company_admin', 'company_pm', 'unit_manager')


def manages_unit(user, unit) -> bool:
    """آیا این کاربر مدیر این واحد است؟"""
    if not unit:
        return False
    if getattr(unit, 'manager_id', None) == user.id:
        return True
    # مدیر واحدی که عضو همان واحد است
    return _role(user) == 'unit_manager' and getattr(user, 'unit_id', None) == unit.id


def can_edit_project(user, project) -> bool:
    """آیا کاربر اجازه‌ی ویرایش این پروژه را دارد؟"""
    if not user or not user.is_authenticated:
        return False
    if is_company_level(user):
        return True
    # سازنده‌ی پروژه
    if getattr(project, 'created_by_id', None) == user.id:
        return True
    # مدیرِ واحدِ صاحب پروژه
    if manages_unit(user, getattr(project, 'owner_unit', None)):
        return True
    return False


def require_can_create_project(user):
    if not can_create_project(user):
        raise PermissionDenied("شما اجازه‌ی ساخت پروژه را ندارید.")


def require_can_edit_project(user, project):
    if not can_edit_project(user, project):
        raise PermissionDenied("شما اجازه‌ی ویرایش این پروژه را ندارید.")


def can_manage_viewers(user, project) -> bool:
    """افزودن/حذفِ مشاهده‌گر (Viewer) فقط توسطِ سازندهٔ پروژه (و سطحِ شرکت به‌عنوان safety net)."""
    if not user or not user.is_authenticated:
        return False
    if is_company_level(user):
        return True
    return getattr(project, 'created_by_id', None) == user.id


def require_can_manage_viewers(user, project):
    if not can_manage_viewers(user, project):
        raise PermissionDenied("تنها سازندهٔ پروژه می‌تواند مشاهده‌گر اضافه یا حذف کند.")
