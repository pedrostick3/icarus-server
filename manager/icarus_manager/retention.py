"""Grandfather-father-son retention for backup files named icarus-YYYYmmdd-HHMMSS-*.zip."""

import re
from datetime import datetime

NAME_RE = re.compile(r"^icarus-(\d{8}-\d{6})-.*\.zip$")


def backup_time(name):
    m = NAME_RE.match(name)
    return datetime.strptime(m.group(1), "%Y%m%d-%H%M%S") if m else None


def to_delete(names, policy):
    """Return the names that fall outside the policy.

    Keeps the `last` newest backups, plus the newest backup of each of the
    last `daily` days, `weekly` ISO weeks and `monthly` months. Names that do
    not look like our backups are never touched. All counts 0 keeps everything.
    """
    dated = sorted(((backup_time(n), n) for n in names if backup_time(n)), reverse=True)
    if not any((policy.last, policy.daily, policy.weekly, policy.monthly)):
        return []

    keep = {name for _, name in dated[:policy.last]}
    buckets = (
        (policy.daily, lambda t: t.date()),
        (policy.weekly, lambda t: t.isocalendar()[:2]),
        (policy.monthly, lambda t: (t.year, t.month)),
    )
    for count, key in buckets:
        seen = set()
        for when, name in dated:
            if len(seen) >= count:
                break
            bucket = key(when)
            if bucket not in seen:
                seen.add(bucket)
                keep.add(name)
    return [name for _, name in dated if name not in keep]
