"""
File Upload Validators
-----------------------
اعتبارسنجی فایل‌های آپلودشده در TaskChatMessage.

- حداکثر حجم: 10 مگابایت
- پسوندهای مجاز: تصاویر، PDF، فایل‌های Office، متن ساده، فشرده
- Content-type چک می‌شود تا کاربر نتواند فایل مخرب را با پسوند عوض‌کرده آپلود کند
"""

import os
from django.core.exceptions import ValidationError

# ── تنظیمات ──────────────────────────────────────────────────────────────────

# حداکثر حجم فایل: 10 مگابایت
MAX_UPLOAD_SIZE_MB = 10
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# پسوندهای مجاز (با حروف کوچک، بدون نقطه)
ALLOWED_EXTENSIONS = {
    # تصاویر
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg',
    # اسناد
    'pdf',
    'doc', 'docx',
    'xls', 'xlsx',
    'ppt', 'pptx',
    # متن
    'txt', 'csv', 'md',
    # فشرده
    'zip', 'rar', '7z',
    # XML (برای export های MS Project)
    'xml',
}

# نگاشت پسوند → content-type های مجاز
ALLOWED_CONTENT_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml',
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/plain', 'text/csv', 'text/markdown',
    'application/zip',
    'application/x-rar-compressed', 'application/vnd.rar',
    'application/x-7z-compressed',
    'application/xml', 'text/xml',
    # مرورگرها گاهی content-type نامشخص می‌فرستند
    'application/octet-stream',
}


# ── Validators ───────────────────────────────────────────────────────────────

def validate_file_size(file):
    """حجم فایل نباید از MAX_UPLOAD_SIZE_MB مگابایت بیشتر باشد."""
    if file.size > MAX_UPLOAD_SIZE_BYTES:
        raise ValidationError(
            f"حجم فایل نباید بیشتر از {MAX_UPLOAD_SIZE_MB} مگابایت باشد. "
            f"حجم فایل شما: {file.size / (1024 * 1024):.1f} مگابایت."
        )


def validate_file_extension(file):
    """پسوند فایل باید در لیست پسوندهای مجاز باشد."""
    ext = os.path.splitext(file.name)[1].lstrip('.').lower()
    if not ext:
        raise ValidationError("فایل باید دارای پسوند باشد.")
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"پسوند '{ext}' مجاز نیست. "
            f"پسوندهای مجاز: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
        )


def validate_file_content_type(file):
    """
    Content-type فایل باید در لیست مجاز باشد.
    از آپلود فایل‌های مخرب با پسوند عوض‌شده جلوگیری می‌کند.
    """
    content_type = getattr(file, 'content_type', None)
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            f"نوع فایل '{content_type}' مجاز نیست."
        )


def validate_chat_file(file):
    """
    Validator ترکیبی برای فایل‌های چت:
    اندازه + پسوند + content-type را با هم چک می‌کند.
    """
    validate_file_size(file)
    validate_file_extension(file)
    validate_file_content_type(file)
