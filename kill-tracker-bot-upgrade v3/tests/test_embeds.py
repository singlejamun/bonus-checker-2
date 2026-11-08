import unittest

from bot.embeds import (
    build_leaderboard_embed,
    build_me_embed,
    build_player_embeds,
    build_report_embeds,
    build_review_embed,
    build_test_bonus_embeds,
    build_top_embed,
    progress_bar,
)

A, B = 111111111111111111, 222222222222222222


def sub(**kw):
    base = dict(id=7, target_name="Alpha", source_channel_id=10, submitter_id=99, jump_url="http://x",
                created_at=1_700_000_000, price=100_000, price_source="message", shots=["ss1.png"],
                status="pending", decided_by=None, decided_at=None, reject_reason=None)
    base.update(kw)
    return base


class ReviewEmbedTests(unittest.TestCase):
    def test_pending_card_shows_math_and_screenshot(self):
        e = build_review_embed(sub(), [{"user_id": A, "kills": 5}, {"user_id": B, "kills": 2}])
        self.assertIn("**5** × 100,000 = **500,000**", e.description)
        self.assertIn("**2** × 100,000 = **200,000**", e.description)
        self.assertEqual(e.image, "attachment://ss1.png")
        total = dict((n, v) for n, v, _ in e.fields)["Total"]
        self.assertIn("7", total)
        self.assertIn("700,000", total)
        self.assertEqual(e.footer, "Pending review")

    def test_missing_screenshot_warns(self):
        e = build_review_embed(sub(shots=[]), [{"user_id": A, "kills": 1}])
        self.assertIsNone(e.image)
        self.assertTrue(any("Screenshot" in n for n, _, _ in e.fields))

    def test_rejected_shows_reason(self):
        e = build_review_embed(sub(status="rejected", decided_by=5, decided_at=1, reject_reason="fake"),
                               [{"user_id": A, "kills": 1}])
        self.assertTrue(any("fake" in v for _, v, _ in e.fields))

    def test_default_price_label(self):
        e = build_review_embed(sub(price=50_000, price_source="default"), [{"user_id": A, "kills": 1}])
        self.assertIn("default price", dict((n, v) for n, v, _ in e.fields)["Price per kill"])


class ReportEmbedTests(unittest.TestCase):
    ROWS = [
        {"target_name": "Alpha", "user_id": A, "kills": 10, "amount": 500_000},
        {"target_name": "Alpha", "user_id": B, "kills": 3, "amount": 150_000},
        {"target_name": "Beta", "user_id": A, "kills": 2, "amount": 200_000},
    ]

    def test_empty(self):
        out = build_report_embeds("T", "window", [], pending=2)
        self.assertEqual(len(out), 1)
        self.assertIn("No approved kills", out[0].description)
        self.assertIn("2", out[0].footer)

    def test_sections_and_all_targets_summary(self):
        out = build_report_embeds("T", "window", self.ROWS)
        titles = [e.title for e in out]
        self.assertEqual(titles, ["T · Alpha", "T · Beta", "T · All targets"])
        all_desc = out[-1].description
        self.assertIn(f"<@{A}> — **12** kills · 700,000", all_desc)   # 10 + 2
        self.assertIn("**Total:** 15 kills · 850,000", all_desc)
        self.assertTrue(all_desc.index(f"<@{A}>") < all_desc.index(f"<@{B}>"))  # ranked

    def test_single_target_has_no_summary_duplicate(self):
        out = build_report_embeds("T", "window", self.ROWS[:2])
        self.assertEqual(len(out), 1)

    def test_pagination_for_many_players(self):
        rows = [{"target_name": "Alpha", "user_id": 1000 + i, "kills": 1, "amount": 50_000} for i in range(300)]
        out = build_report_embeds("T", "window", rows)
        self.assertGreater(len(out), 1)
        self.assertTrue(all(len(e.description) <= 4096 for e in out))

    def test_player_embed(self):
        out = build_player_embeds("T", "window", A, [self.ROWS[0], self.ROWS[2]], pending=1)
        self.assertIn("**Total:** 12 kills · 700,000", out[0].description)
        self.assertIn("1", out[0].footer)


class TestBonusEmbedTests(unittest.TestCase):
    ROWS = ReportEmbedTests.ROWS

    def test_all_players_with_breakdown(self):
        out = build_test_bonus_embeds("T", "window", self.ROWS, pending=2)
        d = out[0].description
        self.assertIn("TEST RUN", d)
        self.assertIn(f"<@{A}> — **12** kills · **700,000**", d)
        self.assertIn("↳ Alpha: 10 kills · 500,000", d)
        self.assertIn("↳ Beta: 2 kills · 200,000", d)
        self.assertIn(f"<@{B}> — **3** kills · **150,000**", d)
        self.assertNotIn("↳ Alpha: 3", d)  # single-target player has no breakdown
        self.assertIn("2 players", d)
        self.assertIn("2", out[-1].footer)

    def test_empty(self):
        self.assertIn("No approved kills", build_test_bonus_embeds("T", "w", [])[0].description)

    def test_many_players_paginates(self):
        rows = [{"target_name": "Alpha", "user_id": 1000 + i, "kills": 1, "amount": 50_000} for i in range(300)]
        out = build_test_bonus_embeds("T", "w", rows)
        self.assertGreater(len(out), 1)
        self.assertTrue(all(len(e.description) <= 4096 for e in out))


class LeaderboardEmbedTests(unittest.TestCase):
    ROWS = [{"user_id": 1000 + i, "kills": 20 - i, "amount": (20 - i) * 50_000} for i in range(6)]

    def test_empty(self):
        self.assertIn("No approved kills", build_leaderboard_embed("T", "w", []).description)

    def test_podium_columns_order_is_2nd_1st_3rd(self):
        e = build_leaderboard_embed("T", "w", self.ROWS)
        names = [n for n, _, _ in e.fields[:3]]
        self.assertEqual(names, ["🥈 2nd", "🥇 1st", "🥉 3rd"])
        self.assertTrue(all(inline for _, _, inline in e.fields[:3]))
        vals = dict((n, v) for n, v, _ in e.fields)
        self.assertIn("<@1000>", vals["🥇 1st"])
        self.assertIn("<@1001>", vals["🥈 2nd"])
        self.assertIn("<@1002>", vals["🥉 3rd"])
        self.assertIn("1,000,000", vals["🥇 1st"])

    def test_rest_listed_below(self):
        e = build_leaderboard_embed("T", "w", self.ROWS)
        rank_field = [v for n, v, _ in e.fields if "Rankings" in n][0]
        self.assertIn("` 4.` <@1003>", rank_field)
        self.assertIn("` 6.` <@1005>", rank_field)

    def test_fewer_than_three_players(self):
        e = build_leaderboard_embed("T", "w", self.ROWS[:1])
        vals = dict((n, v) for n, v, _ in e.fields)
        self.assertEqual(vals["🥈 2nd"], "—")
        self.assertEqual(vals["🥉 3rd"], "—")

    def test_viewer_outside_list(self):
        e = build_leaderboard_embed("T", "w", self.ROWS, viewer_id=5, viewer_rank=23,
                                    viewer_row={"user_id": 5, "kills": 2, "amount": 100_000},
                                    summary={"members": 30, "kills": 100, "amount": 5_000_000})
        vals = dict((n, v) for n, v, _ in e.fields)
        self.assertIn("#23", vals["📍 Your position"])
        self.assertIn("You are #23", e.footer)
        self.assertIn("30 players", e.footer)

    def test_fifteen_rows_fit_discord_limits(self):
        rows = [{"user_id": 100000000000000000 + i, "kills": 50 - i, "amount": 999_999_999} for i in range(15)]
        e = build_leaderboard_embed("T", "w", rows)
        self.assertTrue(all(len(v) <= 1024 for _, v, _ in e.fields))

    def test_progress_bar(self):
        self.assertEqual(progress_bar(10, 10), "▰" * 10)
        self.assertEqual(progress_bar(0, 10), "▱" * 10)
        self.assertEqual(progress_bar(1, 100).count("▰"), 1)


class TopEmbedTests(unittest.TestCase):
    ROWS = [{"user_id": 1000 + i, "kills": 20 - i, "amount": (20 - i) * 50_000} for i in range(15)]

    def test_lists_all_15_in_order(self):
        e = build_top_embed("T", "w", self.ROWS)
        d = e.description
        self.assertIn("🥇 <@1000>", d)
        self.assertIn("🥉 <@1002>", d)
        self.assertIn("`15.` <@1014>", d)
        self.assertLess(d.index("<@1000>"), d.index("<@1014>"))
        self.assertLessEqual(len(d), 4096)

    def test_viewer_below_list_is_appended(self):
        e = build_top_embed("T", "w", self.ROWS, viewer_id=77, viewer_rank=31,
                            viewer_row={"user_id": 77, "kills": 1, "amount": 50_000})
        self.assertIn("📍 `31.` <@77>", e.description)
        self.assertIn("You are #31", e.footer)

    def test_empty(self):
        self.assertIn("No approved kills", build_top_embed("T", "w", []).description)


class MeEmbedTests(unittest.TestCase):
    ROWS = ReportEmbedTests.ROWS

    def card(self, **kw):
        base = dict(display_name="Rahul", avatar_url="http://a/b.png", rank=2, players=14,
                    ahead={"user_id": 5, "kills": 13}, behind={"user_id": 6, "kills": 10},
                    leader={"user_id": 5, "kills": 13}, pending=1)
        base.update(kw)
        return build_me_embed("T", "w", A, [self.ROWS[0], self.ROWS[2]], **base)

    def test_rival_card(self):
        e = self.card()
        vals = dict((n, v) for n, v, _ in e.fields)
        self.assertIn("12", vals["🎯 Kills"])
        self.assertIn("700,000", vals["💰 Earned"])
        self.assertIn("<@5>", vals["⬆️ Ahead of you"])
        self.assertIn("**1 kill** more", vals["⬆️ Ahead of you"])
        self.assertIn("<@6>", vals["⬇️ Behind you"])
        self.assertIn("**2 kills** fewer", vals["⬇️ Behind you"])
        self.assertIn("92%", vals["📊 Progress to #1"])
        self.assertIn("#2", e.description)
        self.assertEqual(e.author, "Rahul")
        self.assertIn("1", e.footer)

    def test_leader_has_no_one_ahead(self):
        e = self.card(rank=1, ahead=None, leader={"user_id": A, "kills": 12})
        names = [n for n, _, _ in e.fields]
        self.assertIn("👑 You are #1", names)
        self.assertNotIn("⬆️ Ahead of you", names)

    def test_last_place_has_no_one_behind(self):
        e = self.card(behind=None)
        self.assertNotIn("⬇️ Behind you", [n for n, _, _ in e.fields])

    def test_tie_wording(self):
        e = self.card(ahead={"user_id": 5, "kills": 12})
        vals = dict((n, v) for n, v, _ in e.fields)
        self.assertIn("same kills", vals["⬆️ Ahead of you"])

    def test_no_kills(self):
        e = build_me_embed("T", "w", A, [], display_name="Rahul")
        self.assertIn("No approved kills", e.description)


if __name__ == "__main__":
    unittest.main()
