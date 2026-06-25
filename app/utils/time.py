from datetime import datetime, timedelta, timezone


SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat()


def unix_timestamp() -> int:
    return int(datetime.now(SHANGHAI_TZ).timestamp())
