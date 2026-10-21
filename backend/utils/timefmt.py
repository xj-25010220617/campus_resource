"""统一业务时区时间格式化与日期区间计算。

约定：
- 数据库在 USE_TZ=True 下保存 UTC aware datetime，ORM 读出的值也是 UTC aware；
- 面向用户展示的时间必须先转换到 TIME_ZONE（当前为 Asia/Shanghai）再格式化；
- 日期区间统一使用「左闭右开」，避免跨天/跨月/跨年重复或遗漏；
- naive datetime 按项目业务时区解释并记录 warning，不静默当作 UTC。
"""
import logging
from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def business_timezone():
    """返回项目配置的业务时区。"""
    return timezone.get_current_timezone()


def to_business_time(value) -> Optional[datetime]:
    """把 datetime/date 转换到业务时区；空值返回 None，非法值返回 None。"""
    if value is None or value == '':
        return None
    try:
        if isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, time.min)
        if not isinstance(value, datetime):
            logger.warning('时间字段类型不支持: %r', type(value))
            return None
        if timezone.is_naive(value):
            # 项目约定时间是 aware；naive 值只可能来自旧数据或裸 SQL，
            # 按业务时区解释，避免被误当 UTC 后再次偏移。
            logger.warning('检测到 naive datetime，按业务时区解释: %s', value)
            if settings.USE_TZ:
                return timezone.make_aware(value, business_timezone())
            return value
        if settings.USE_TZ:
            return timezone.localtime(value)
        return value
    except Exception as exc:  # 时间展示不能影响主流程
        logger.warning('时间转换失败 value=%r: %s', value, exc)
        return None


def format_business_time(value, fmt: str = '%Y-%m-%d %H:%M:%S') -> str:
    """格式化为业务时区展示字符串；空值/非法值返回空串。"""
    converted = to_business_time(value)
    if converted is None:
        return ''
    try:
        return converted.strftime(fmt)
    except Exception as exc:
        logger.warning('时间格式化失败 value=%r: %s', value, exc)
        return ''


def to_iso(value) -> Optional[str]:
    """返回带业务时区偏移的 ISO 8601 字符串；空值返回 None。"""
    converted = to_business_time(value)
    return converted.isoformat() if converted is not None else None


def _make_aware(dt: datetime) -> datetime:
    if settings.USE_TZ:
        return timezone.make_aware(dt, business_timezone())
    return dt


def day_bounds(day: date) -> Tuple[datetime, datetime]:
    """本地自然日 [00:00, 次日 00:00)。"""
    start = _make_aware(datetime.combine(day, time.min))
    end = _make_aware(datetime.combine(day + timedelta(days=1), time.min))
    return start, end


def month_bounds(year: int, month: int) -> Tuple[datetime, datetime]:
    """本地自然月 [1 日 00:00, 下月 1 日 00:00)。"""
    start = _make_aware(datetime(year, month, 1))
    if month == 12:
        end = _make_aware(datetime(year + 1, 1, 1))
    else:
        end = _make_aware(datetime(year, month + 1, 1))
    return start, end


def year_bounds(year: int) -> Tuple[datetime, datetime]:
    """本地自然年 [1 月 1 日 00:00, 次年 1 月 1 日 00:00)。"""
    return _make_aware(datetime(year, 1, 1)), _make_aware(datetime(year + 1, 1, 1))


def recent_days_bounds(days: int, today: Optional[date] = None) -> Tuple[datetime, datetime]:
    """最近 N 个自然日（含今天），左闭右开。"""
    days = max(int(days), 1)
    today = today or timezone.localdate()
    start_day = today - timedelta(days=days - 1)
    start = _make_aware(datetime.combine(start_day, time.min))
    end = _make_aware(datetime.combine(today + timedelta(days=1), time.min))
    return start, end


def parse_bound(value) -> Optional[datetime]:
    """把路由参数中的日期/时间字符串解析为业务时区 aware datetime。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return to_business_time(value)
    if isinstance(value, date):
        return day_bounds(value)[0]
    from django.utils.dateparse import parse_date, parse_datetime

    text = str(value).strip()
    dt = parse_datetime(text)
    if dt is None:
        parsed_date = parse_date(text)
        if parsed_date is not None:
            dt = datetime.combine(parsed_date, time.min)
    if dt is None:
        return None
    return to_business_time(dt)
