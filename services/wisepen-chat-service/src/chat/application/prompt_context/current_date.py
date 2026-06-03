from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE_NAME = "Asia/Shanghai"


def build_current_date_context() -> str:
    now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE_NAME))
    current_date = now.date().isoformat()
    weekday = now.strftime("%A")
    return (
        f"Current date: {current_date} ({weekday}), timezone baseline: "
        f"{DEFAULT_TIMEZONE_NAME}.\n"
        "Use this date as the anchor for relative date expressions such as "
        '"recently", "today", "yesterday", "this week", "this year", '
        '"最近", "今天", "昨天", "本周", and "今年".\n'
        "The system only provides date-level temporal context. Do not assume "
        "precise current clock time or cross-timezone time conversion unless it is "
        "explicitly provided by the user."
    )