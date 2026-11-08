import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.timeutil import week_window

IST = ZoneInfo("Asia/Kolkata")


def ist(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST)


class WeekWindowTests(unittest.TestCase):
    def test_midweek(self):
        # Wed 14 Oct 2026 -> window Sun 11 Oct 08:00 .. Sun 18 Oct 08:00
        s, e = week_window(ist(2026, 10, 14, 15), IST, 8)
        self.assertEqual((s, e), (ist(2026, 10, 11, 8), ist(2026, 10, 18, 8)))

    def test_sunday_before_start_hour_belongs_to_previous_week(self):
        s, e = week_window(ist(2026, 10, 11, 7, 59), IST, 8)
        self.assertEqual((s, e), (ist(2026, 10, 4, 8), ist(2026, 10, 11, 8)))

    def test_sunday_exactly_at_start_hour_starts_new_week(self):
        s, e = week_window(ist(2026, 10, 11, 8, 0), IST, 8)
        self.assertEqual((s, e), (ist(2026, 10, 11, 8), ist(2026, 10, 18, 8)))

    def test_saturday_night(self):
        s, e = week_window(ist(2026, 10, 17, 23, 59), IST, 8)
        self.assertEqual(s, ist(2026, 10, 11, 8))

    def test_weeks_back(self):
        s, e = week_window(ist(2026, 10, 14, 15), IST, 8, weeks_back=1)
        self.assertEqual((s, e), (ist(2026, 10, 4, 8), ist(2026, 10, 11, 8)))

    def test_accepts_utc_input(self):
        now = datetime(2026, 10, 11, 2, 30, tzinfo=timezone.utc)  # = 08:00 IST Sunday
        s, _ = week_window(now, IST, 8)
        self.assertEqual(s, ist(2026, 10, 11, 8))

    def test_dst_zone_keeps_local_8am(self):
        ny = ZoneInfo("America/New_York")
        # DST ends Sun 1 Nov 2026; the week Oct 25 -> Nov 1 is 7 days + 1h long but still 8am to 8am local
        s, e = week_window(datetime(2026, 10, 28, 12, tzinfo=ny), ny, 8)
        self.assertEqual((s.hour, e.hour), (8, 8))
        self.assertEqual(e.timestamp() - s.timestamp(), 7 * 86400 + 3600)


if __name__ == "__main__":
    unittest.main()
