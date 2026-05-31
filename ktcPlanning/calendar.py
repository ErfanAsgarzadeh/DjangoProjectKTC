"""
موتور تقویم کاری
─────────────────
وظیفه: با گرفتن (start, duration_hours, calendar) بگوید finish دقیقاً کِی است.

ورودی‌ها:
  - start        : datetime — لحظه شروع تسک
  - duration_hours: Decimal/float — حجم کار (ساعت خالص)
  - calendar     : Calendar instance — با prefetch intervals و exceptions

خروجی:
  - finish       : datetime — لحظه پایان واقعی بر اساس تقویم

مثال:
  start = شنبه ۰۸:۰۰
  duration = ۲۰ ساعت
  تقویم: شنبه تا چهارشنبه ۰۸-۱۷ (۹ ساعت روزانه)
  → finish = دوشنبه ۱۷:۰۰  (شنبه ۹h + یکشنبه ۹h + دوشنبه ۲h)
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Calendar


# ─────────────────────────────────────────
# ساختار داخلی یک روز کاری
# ─────────────────────────────────────────

@dataclass
class DaySchedule:
    """بازه‌های کاری یک روز مشخص."""
    date: datetime.date
    intervals: list[tuple[datetime.time, datetime.time]] = field(default_factory=list)

    @property
    def total_hours(self) -> float:
        total = 0.0
        for start, end in self.intervals:
            s = datetime.datetime.combine(self.date, start)
            e = datetime.datetime.combine(self.date, end)
            total += (e - s).total_seconds() / 3600
        return total

    def is_working(self) -> bool:
        return bool(self.intervals)

    def hours_from(self, from_time: datetime.time) -> float:
        """ساعات کاری باقیمانده از یک لحظه تا پایان روز."""
        total = 0.0
        for start, end in self.intervals:
            effective_start = max(start, from_time)
            if effective_start < end:
                s = datetime.datetime.combine(self.date, effective_start)
                e = datetime.datetime.combine(self.date, end)
                total += (e - s).total_seconds() / 3600
        return total

    def time_after_hours(self, from_time: datetime.time, hours: float) -> datetime.time | None:
        """بعد از N ساعت کار از from_time، ساعت چند است؟ None اگر از روز رد شود."""
        remaining = hours
        for start, end in self.intervals:
            effective_start = max(start, from_time)
            if effective_start >= end:
                continue
            s = datetime.datetime.combine(self.date, effective_start)
            e = datetime.datetime.combine(self.date, end)
            slot_hours = (e - s).total_seconds() / 3600
            if remaining <= slot_hours:
                result = s + datetime.timedelta(hours=remaining)
                return result.time()
            remaining -= slot_hours
        return None  # ساعات از این روز بیشتر است


# ─────────────────────────────────────────
# کلاس اصلی موتور تقویم
# ─────────────────────────────────────────

class CalendarEngine:
    """
    موتور تقویم کاری.

    استفاده:
        engine = CalendarEngine(calendar)
        finish = engine.add_working_hours(start, 40)
        hours  = engine.working_hours_between(start, finish)
    """

    MAX_SEARCH_DAYS = 1_825  # ۵ سال — حد امنیتی برای جلوگیری از حلقه بی‌نهایت

    def __init__(self, calendar: "Calendar"):
        self.calendar = calendar
        self._intervals: dict[int, list[tuple]] = {}   # weekday → [(start, end)]
        self._exceptions: dict[datetime.date, list[tuple] | None] = {}  # date → intervals یا None
        self._load_calendar()

    def _load_calendar(self) -> None:
        """بارگذاری بازه‌های کاری و استثناها از دیتابیس به حافظه."""
        for interval in self.calendar.intervals.all():
            self._intervals.setdefault(interval.weekday, []).append(
                (interval.start_time, interval.end_time)
            )
        # مرتب‌سازی هر روز بر اساس ساعت شروع
        for wd in self._intervals:
            self._intervals[wd].sort()

        for exc in self.calendar.exceptions.all():
            if exc.is_working:
                # روز استثنای کاری — بازه پیش‌فرض ۰۸-۱۷
                self._exceptions[exc.date] = [(
                    datetime.time(8, 0),
                    datetime.time(17, 0),

)]
            else:
                # تعطیل — هیچ بازه‌ای
                self._exceptions[exc.date] = None

    def get_day_schedule(self, date: datetime.date) -> DaySchedule:
        """برنامه کاری یک روز مشخص را برمی‌گرداند."""
        if date in self._exceptions:
            exc = self._exceptions[date]
            intervals = exc if exc is not None else []
        else:
            intervals = self._intervals.get(date.weekday(), [])
        return DaySchedule(date=date, intervals=intervals)

    def add_working_hours(
        self,
        start: datetime.datetime,
        hours: float | Decimal,
    ) -> datetime.datetime:
        """
        از لحظه start به اندازه hours ساعت کاری جلو برو و finish را برگردان.
        تعطیلات و بازه‌های غیرکاری نادیده گرفته می‌شوند.
        """
        hours = float(hours)
        if hours <= 0:
            return start

        current_date = start.date()
        current_time = start.time()
        remaining = hours
        days_searched = 0

        while remaining > 0:
            if days_searched > self.MAX_SEARCH_DAYS:
                raise RuntimeError(
                    f"CalendarEngine: بیش از {self.MAX_SEARCH_DAYS} روز بدون یافتن ساعت کاری. "
                    "لطفاً تقویم را بررسی کنید."
                )

            schedule = self.get_day_schedule(current_date)

            if not schedule.is_working():
                # روز تعطیل — رد شو
                current_date += datetime.timedelta(days=1)
                current_time = datetime.time(0, 0)
                days_searched += 1
                continue

            available = schedule.hours_from(current_time)

            if available <= 0:
                # ساعت کاری این روز تمام شده
                current_date += datetime.timedelta(days=1)
                current_time = datetime.time(0, 0)
                days_searched += 1
                continue

            if remaining <= available:
                # کار در همین روز تمام می‌شود
                finish_time = schedule.time_after_hours(current_time, remaining)
                return datetime.datetime.combine(current_date, finish_time)

            # کار به روز بعد می‌کشد
            remaining -= available
            current_date += datetime.timedelta(days=1)
            current_time = datetime.time(0, 0)
            days_searched += 1

        return datetime.datetime.combine(current_date, current_time)

    def working_hours_between(
        self,
        start: datetime.datetime,
        finish: datetime.datetime,
    ) -> float:
        """
        تعداد ساعات کاری خالص بین دو لحظه را برمی‌گرداند.
        برای محاسبه duration واقعی تسک استفاده می‌شود.
        """
        if finish <= start:
            return 0.0

        total = 0.0
        current_date = start.date()
        end_date = finish.date()

        while current_date <= end_date:
            schedule = self.get_day_schedule(current_date)

            if not schedule.is_working():
                current_date += datetime.timedelta(days=1)
                continue

            from_time = start.time() if current_date == start.date() else datetime.time(0, 0)
            to_time   = finish.time() if current_date == end_date else datetime.time(23, 59, 59)

            for iv_start, iv_end in schedule.intervals:
                eff_start = max(iv_start, from_time)
                eff_end   = min(iv_end, to_time)
                if eff_start < eff_end:
                    s = datetime.datetime.combine(current_date, eff_start)
                    e = datetime.datetime.combine(current_date, eff_end)
                    total += (e - s).total_seconds() / 3600

            current_date += datetime.timedelta(days=1)

        return total

    def subtract_working_hours(
        self,
        finish: datetime.datetime,
        hours: float | Decimal,
    ) -> datetime.datetime:
        """
        از finish به اندازه hours ساعت کاری عقب برو و start را برگردان.
        برای backward pass در CPM استفاده می‌شود.
        """
        hours = float(hours)
        if hours <= 0:
            return finish

        current_date = finish.date()
        current_time = finish.time()
        remaining = hours
        days_searched = 0

        while remaining > 0:
            if days_searched > self.MAX_SEARCH_DAYS:
                raise RuntimeError("subtract_working_hours: تقویم را بررسی کنید.")

            schedule = self.get_day_schedule(current_date)

            if not schedule.is_working():
                current_date -= datetime.timedelta(days=1)
                current_time = datetime.time(23, 59, 59)
                days_searched += 1
                continue

            # ساعات کاری این روز تا current_time
            available = 0.0
            for iv_start, iv_end in reversed(schedule.intervals):
                eff_end = min(iv_end, current_time)
                if eff_end <= iv_start:
                    continue
                s = datetime.datetime.combine(current_date, iv_start)
                e = datetime.datetime.combine(current_date, eff_end)
                available += (e - s).total_seconds() / 3600

            if available <= 0:
                current_date -= datetime.timedelta(days=1)
                current_time = datetime.time(23, 59, 59)
                days_searched += 1
                continue

            if remaining <= available:
                # نقطه شروع در همین روز است
                for iv_start, iv_end in reversed(schedule.intervals):
                    eff_end = min(iv_end, current_time)
                    if eff_end <= iv_start:
                        continue
                    s = datetime.datetime.combine(current_date, iv_start)
                    e = datetime.datetime.combine(current_date, eff_end)
                    slot = (e - s).total_seconds() / 3600
                    if remaining <= slot:
                        result = e - datetime.timedelta(hours=remaining)
                        return result
                    remaining -= slot

            remaining -= available
            current_date -= datetime.timedelta(days=1)
            current_time = datetime.time(23, 59, 59)
            days_searched += 1

        return datetime.datetime.combine(current_date, current_time)

    def next_working_moment(self, dt: datetime.datetime) -> datetime.datetime:
        """
        اگر dt در یک لحظه غیرکاری باشد، اولین لحظه کاری بعد از آن را برمی‌گرداند.
        برای snap کردن planned_start به بازه کاری استفاده می‌شود.
        """
        current_date = dt.date()
        current_time = dt.time()

        for _ in range(self.MAX_SEARCH_DAYS):
            schedule = self.get_day_schedule(current_date)
            if not schedule.is_working():
                current_date += datetime.timedelta(days=1)
                current_time = datetime.time(0, 0)
                continue

            for iv_start, iv_end in schedule.intervals:
                if current_time <= iv_start:
                    return datetime.datetime.combine(current_date, iv_start)
                if current_time < iv_end:
                    return datetime.datetime.combine(current_date, current_time)

            # ساعت این روز گذشته — برو روز بعد
            current_date += datetime.timedelta(days=1)
            current_time = datetime.time(0, 0)

        raise RuntimeError("next_working_moment: هیچ روز کاری پیدا نشد.")


# ─────────────────────────────────────────
# توابع کمکی سطح بالا
# ─────────────────────────────────────────

def compute_finish(
    task_version,
    calendar: "Calendar | None" = None,
) -> datetime.datetime:
    """
    planned_finish یک TaskVersion را محاسبه و ذخیره می‌کند.

    اگر calendar پاس داده نشود از calendar خود task_version استفاده می‌کند.
    اگر هیچ‌کدام نباشد، ساعت کاری پیش‌فرض (۸ ساعت/روز) فرض می‌شود.
    """
    cal = calendar or task_version.calendar
    if cal is None:
        # بدون تقویم: فرض ۸ ساعت روز کاری ۵ روز هفته
        finish = _simple_add_hours(task_version.planned_start, float(task_version.duration_hours))
    else:
        engine = CalendarEngine(cal)
        start = engine.next_working_moment(task_version.planned_start)
        finish = engine.add_working_hours(start, task_version.duration_hours)

    task_version.planned_finish = finish
    task_version.save(update_fields=["planned_finish"])
    return finish


def _simple_add_hours(start: datetime.datetime, hours: float) -> datetime.datetime:
    """فال‌بک بدون تقویم — فقط ۸ ساعت روز، ۵ روز هفته."""
    remaining = hours
    current = start
    while remaining > 0:
        if current.weekday() < 5:  # شنبه تا چهارشنبه در تقویم میلادی = Mon-Fri
            day_end = current.replace(hour=17, minute=0, second=0, microsecond=0)
            available = max(0, (day_end - current).total_seconds() / 3600)
            if remaining <= available:
                return current + datetime.timedelta(hours=remaining)
            remaining -= available
        current = (current + datetime.timedelta(days=1)).replace(
            hour=8, minute=0, second=0, microsecond=0
        )
    return current