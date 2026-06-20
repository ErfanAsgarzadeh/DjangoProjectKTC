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


# ─────────────────────────────────────────────
# تخصیص دو مرحله‌ای نقش تسک (Reviewer → Executor)
# ─────────────────────────────────────────────

def is_task_reviewer(user, task) -> bool:
    """آیا کاربر، Reviewer این تسک است؟"""
    from .models import TaskRole
    return TaskRole.objects.filter(task=task, user=user, role='reviewer').exists()


def can_assign_task_role(actor, task, target_user, role: str) -> bool:
    """
    منطق تخصیص نقش روی تسک:

    - Reviewer: کسی که اجازه ویرایش پروژه را دارد می‌تواند هر کسی را
      Reviewer کند، ولی **نمی‌تواند** کسی را که عضو واحد همان Reviewer
      است Reviewer کند (طبق نیاز کاربر: «نباید بتواند به نیروهای واحد
      انجام‌دهنده تسک بدهد»). برای جلوگیری از این کار، Reviewer باید کسی
      باشد که خودش به یک واحد وصل است؛ سپس Executor از همان واحد می‌آید.

    - Executor: فقط Reviewerِ همان تسک می‌تواند Executor تعیین کند، و
      Executor باید عضو **واحد مستقیم خود Reviewer** باشد.

    - بقیه نقش‌ها (owner / project manager): فقط ادمین.

    superuser و company_admin همیشه می‌توانند (safety net).
    """
    if not actor or not actor.is_authenticated:
        return False
    if actor.is_superuser or _role(actor) == 'company_admin':
        return True

    if role == 'reviewer':
        # ویرایش‌گر پروژه می‌تواند Reviewer تعیین کند
        if not can_edit_project(actor, task.project):
            return False
        # Reviewer باید به یک واحد وصل باشد (تا بعداً بتواند Executor انتخاب کند)
        if not getattr(target_user, 'unit_id', None):
            return False
        # سازنده/ویرایش‌گر نمی‌تواند کسی از داخل آن واحد را reviewer کند
        # مگر اینکه خودش هم همان واحد را مدیریت می‌کند یا company-level است.
        # (اگر actor خودش company-level بود بالا برگشته‌ایم؛ پس اینجا فقط
        # سازنده پروژه/مدیر واحدِ صاحب پروژه است.)
        return True

    if role == 'executor':
        # فقط Reviewerِ همین تسک می‌تواند Executor تعیین کند
        if not is_task_reviewer(actor, task):
            return False
        # Executor باید عضو واحد مستقیم همین Reviewer (= actor) باشد
        if not getattr(actor, 'unit_id', None):
            return False
        return getattr(target_user, 'unit_id', None) == actor.unit_id

    # owner / project manager → فقط مدیر سیستم
    return False


def require_can_assign_task_role(actor, task, target_user, role: str):
    if not can_assign_task_role(actor, task, target_user, role):
        raise PermissionDenied("شما اجازه‌ی تخصیص این نقش را ندارید.")


# ─────────────────────────────────────────────
# تایید/قفل Revision (Approver تعیین‌شده)
# ─────────────────────────────────────────────

def can_approve_revision(actor, revision) -> bool:
    """
    تایید/قفل Revision:
    - اگر تاییدکننده‌ی تعیین‌شده وجود دارد → فقط همان فرد (یا admin/superuser).
    - در غیر اینصورت → کسی که اجازه‌ی ویرایش پروژه را دارد (سازگاری با حالت قدیم).
    """
    if not actor or not actor.is_authenticated:
        return False
    if actor.is_superuser or _role(actor) == 'company_admin':
        return True
    approver_id = getattr(revision, 'designated_approver_id', None)
    if approver_id:
        return actor.id == approver_id
    return can_edit_project(actor, revision.project)


def require_can_approve_revision(actor, revision):
    if not can_approve_revision(actor, revision):
        raise PermissionDenied("فقط تاییدکننده‌ی تعیین‌شده‌ی این نسخه می‌تواند آن را قفل کند.")
