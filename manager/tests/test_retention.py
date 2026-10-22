import unittest
from datetime import datetime, timedelta

from icarus_manager.config import Retention
from icarus_manager.retention import to_delete


def names_every(hours, count, start=datetime(2026, 10, 22, 12, 0)):
    return [f"icarus-{(start - timedelta(hours=hours * i)):%Y%m%d-%H%M%S}-x.zip" for i in range(count)]


class RetentionTest(unittest.TestCase):
    def test_keeps_last_n(self):
        names = names_every(1, 10)
        deleted = to_delete(names, Retention(last=3, daily=0, weekly=0, monthly=0))
        self.assertEqual(sorted(set(names) - set(deleted)), sorted(names[:3]))

    def test_daily_keeps_newest_of_each_day(self):
        names = names_every(6, 4 * 10)  # 10 days, 4 per day
        kept = set(names) - set(to_delete(names, Retention(last=0, daily=5, weekly=0, monthly=0)))
        self.assertEqual(len(kept), 5)
        days = {n[7:15] for n in kept}
        self.assertEqual(len(days), 5)
        for day in days:  # newest of that day
            self.assertEqual(max(n for n in names if n[7:15] == day), next(n for n in kept if n[7:15] == day))

    def test_gfs_union_and_foreign_files_untouched(self):
        names = names_every(12, 2 * 90) + ["notes.txt", "icarus-bad.zip"]
        deleted = to_delete(names, Retention(last=4, daily=7, weekly=4, monthly=3))
        self.assertNotIn("notes.txt", deleted)
        self.assertNotIn("icarus-bad.zip", deleted)
        kept = [n for n in names if n not in deleted and n.startswith("icarus-2")]
        self.assertLessEqual(len(kept), 4 + 7 + 4 + 3)
        self.assertIn(names[0], kept)

    def test_all_zero_keeps_everything(self):
        self.assertEqual(to_delete(names_every(1, 5), Retention(0, 0, 0, 0)), [])


if __name__ == "__main__":
    unittest.main()
