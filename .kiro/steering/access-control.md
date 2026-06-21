# مدلِ دسترسی و کنترلِ سطحِ کاربران (RBAC) — تصمیمات و نقشه‌راه

> این سند، منبعِ حقیقتِ (source of truth) تصمیماتِ امنیتی/دسترسیِ پروژهٔ KTC است.
> هدف: حتی اگر تاریخچهٔ چت در دسترس نباشد، همهٔ تصمیمات اینجا محفوظ بماند.

## ساختارِ سازمانیِ واقعی

- یک **واحدِ برنامه‌ریزی** با **یک مدیرِ واحد** وجود دارد.
- در همان واحد، **چند «مدیرِ پروژه» (project_manager)** هستند که هرکدام سبدِ پروژه‌های خودش را مدیریت می‌کند.
- پروژه‌ها غالباً **چندواحدی (cross-unit)** اجرا می‌شوند: پلنر در واحدِ برنامه‌ریزی، مجری‌ها در واحدهای اجرایی (مکانیک/برق/...).

## نقش‌های سازمانی (`CustomUser.org_role`)

| نقش | معنا |
|---|---|
| `company_admin` | مدیر سیستم (سطحِ شرکت) |
| `company_pm` | مدیر پروژه‌های شرکت (سطحِ شرکت) |
| `unit_manager` | مدیرِ واحد — فقط اگر واقعاً `OrgUnit.manager` باشد |
| `project_manager` | مدیرِ پروژه — روی پروژه‌های ساختهٔ خودش |
| `member` | عضو معمولی (مجری/بررسی‌کننده از طریقِ TaskRole) |

`superuser` همیشه دسترسیِ کامل دارد (safety net).

## تصمیماتِ کلیدیِ workflow (تاییدشده توسطِ کاربر)

### ۱) تاییدِ نسخه (Revision) — تک‌مرحله‌ای
- تاییدِ نسخه و تاییدِ گزارش **دو قانونِ جدا** هستند.
- برای پروژهٔ `scope='company'`: **Approverِ پیش‌فرض = مدیرِ واحدِ برنامه‌ریزی** مگر اینکه هنگامِ ساختِ پیش‌نویس به‌صورت دستی فردِ دیگری انتخاب شود.
- برای پروژهٔ `scope='intra_unit'`: Approverِ پیش‌فرض = مدیرِ `owner_unit`؛ در نبودِ آن، سازنده.
- فقط همان Approverِ تعیین‌شده می‌تواند نسخه را قفل کند.

### ۲) تاییدِ گزارشِ تسک (TaskReportLog) — دو‌مرحله‌ای
- جریان: `pending → reviewer_approved → final_approved`.
- درصدِ پیشرفت در `TaskActual` **فقط** هنگامِ `final_approved` ثبت می‌شود.
- پروژهٔ `intra_unit`: `reviewer_approved` به‌طور خودکار = `final_approved`.
- پروژهٔ `company`: گامِ دومِ صریح، فقط توسطِ **مدیرِ واحدِ برنامه‌ریزی** (یا سطحِ شرکت).

### ۳) Bypass تاییدِ Reviewer
- یک تنظیمِ کلیِ سیستم: `SystemSettings.allow_planning_manager_bypass_reviewer` (پیش‌فرض: خاموش).
- اگر روشن باشد، مدیرِ برنامه‌ریزی می‌تواند گزارشِ پروژهٔ شرکتی را در یک action مستقیماً final-approve کند.

### ۴) Delegation (آینده)
- در آینده هر شخص می‌تواند «کارتابلِ» خود را برای مدتی به دیگری بدهد.
- آماده‌سازی: تابعِ `effective_actor_ids(user)` در `permissions.py` نقطهٔ مرکزیِ این قابلیت است؛ افزودنِ delegation فقط همین تابع را تغییر می‌دهد.

## نقشه‌راهِ PRها

| PR | وضعیت | محتوا |
|---|---|---|
| **قبلی** | merged/open | قفلِ ویرایشِ زمان‌بندی، تخصیصِ دومرحله‌ایِ تسک، Approverِ نسخه، مدلِ `ProjectViewer` (PR #1) |
| **PR ۱** | open (PR #2) | وصله‌های critical امنیتی + نقشِ `project_manager` + `OrgUnit.is_planning_unit` + helperها + BasePermission classes + hardening تنظیمات |
| **PR ۲** | برنامه‌ریزی‌شده | Read scoping: `accessible_projects(user)` روی همهٔ ViewSetها + کوچک‌کردنِ `UserListView` |
| **PR ۳** | برنامه‌ریزی‌شده | `Project.scope` + state machine `TaskReportLog` + `SystemSettings` + بازنویسیِ `approve_report` دو‌مرحله‌ای + Approverِ پیش‌فرضِ scope-aware |
| **PR ۴** | برنامه‌ریزی‌شده | `ProjectMembership` (ادغامِ `ProjectViewer` + چند PM) + endpointِ انتقالِ مالکیت + Audit Log |
| **PR ۵** | برنامه‌ریزی‌شده | `GET /auth/me/capabilities/` + سیگنال‌های UI + اسکلتِ مدلِ Delegation + scoping نرخِ منابع |

## وضعیتِ فعلیِ پیاده‌سازی (تا انتهای PR ۱)

- `manages_unit` اصلاح شد: فقط `OrgUnit.manager == user` معتبر است.
- `RegisterView`/`UserProfileView`: نقش/واحد غیرقابلِ تخصیص از مسیرِ عمومی.
- `UserManagementViewSet`: scoped به واحدهای تحتِ مدیریت + سریالایزرِ ادمینیِ privilege-aware.
- `OrgUnitViewSet`: نوشتن فقط سطحِ شرکت.
- helperها: `get_planning_unit`, `get_planning_manager`, `is_planning_manager`, `effective_actor_ids`.

## نکاتِ محیطی

- این sandbox دسترسیِ شبکه برای نصبِ Django ندارد؛ صحتِ مهاجرت‌ها باید به‌صورتِ محلی با
  `python manage.py makemigrations --check --dry-run` و `migrate` تایید شود.
- مهاجرت‌های این مجموعه دستی نوشته شده‌اند (به‌دلیلِ نبودِ امکانِ اجرای `makemigrations`).
