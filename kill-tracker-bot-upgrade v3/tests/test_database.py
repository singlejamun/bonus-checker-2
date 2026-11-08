import unittest

from bot.database import Database


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()

    async def _submission(self, msg_id=1, entries=None, price=50_000, created=1_000):
        return await self.db.create_submission(
            guild_id=1, target_channel_id=10, target_name="Alpha", source_channel_id=10,
            source_message_id=msg_id, submitter_id=99, jump_url="u", price=price,
            price_source="target", entries=entries or {5: 5, 6: 2}, created_at=created,
        )

    async def test_target_roundtrip_and_name_lookup(self):
        await self.db.upsert_target(10, 1, "Alpha", 50_000, [50_000, 1_000_000])
        t = await self.db.get_target(10)
        self.assertEqual((t["name"], t["default_price"], t["prices"]), ("Alpha", 50_000, [50_000, 1_000_000]))
        await self.db.upsert_target(10, 1, "Beta", 70_000, [])
        self.assertEqual((await self.db.get_target(10))["name"], "Beta")
        self.assertTrue(await self.db.target_name_exists(1, "beta"))
        self.assertTrue(await self.db.delete_target(10))
        self.assertIsNone(await self.db.get_target(10))

    async def test_duplicate_message_is_ignored(self):
        self.assertIsNotNone(await self._submission(msg_id=7))
        self.assertIsNone(await self._submission(msg_id=7))

    async def test_accept_logs_every_member_with_amount(self):
        sid = await self._submission(price=100_000)
        self.assertTrue(await self.db.accept(sid, decided_by=1))
        rows = await self.db.totals(1, 0, 10_000)
        by_user = {r["user_id"]: (r["kills"], r["amount"]) for r in rows}
        self.assertEqual(by_user, {5: (5, 500_000), 6: (2, 200_000)})
        self.assertEqual((await self.db.get_submission(sid))["status"], "accepted")

    async def test_cannot_decide_twice(self):
        sid = await self._submission()
        self.assertTrue(await self.db.accept(sid, 1))
        self.assertFalse(await self.db.accept(sid, 2))
        self.assertFalse(await self.db.reject(sid, 2, "late"))
        self.assertEqual(len(await self.db.totals(1, 0, 10_000)), 2)  # logged once

    async def test_reject_stores_reason_and_logs_nothing(self):
        sid = await self._submission()
        self.assertTrue(await self.db.reject(sid, 1, "fake ss"))
        sub = await self.db.get_submission(sid)
        self.assertEqual((sub["status"], sub["reject_reason"]), ("rejected", "fake ss"))
        self.assertEqual(await self.db.totals(1, 0, 10_000), [])

    async def test_edit_kills_add_remove_and_price_then_accept(self):
        sid = await self._submission(entries={5: 5, 6: 2})
        await self.db.set_entry(sid, 5, 8)      # change
        await self.db.set_entry(sid, 6, 0)      # remove
        await self.db.set_entry(sid, 7, 3)      # add member
        await self.db.set_price(sid, 200_000)
        entries = {e["user_id"]: e["kills"] for e in await self.db.get_entries(sid)}
        self.assertEqual(entries, {5: 8, 7: 3})
        await self.db.accept(sid, 1)
        by_user = {r["user_id"]: r["amount"] for r in await self.db.totals(1, 0, 10_000)}
        self.assertEqual(by_user, {5: 1_600_000, 7: 600_000})

    async def test_no_edits_after_decision(self):
        sid = await self._submission()
        await self.db.accept(sid, 1)
        self.assertFalse(await self.db.set_entry(sid, 5, 99))
        self.assertFalse(await self.db.set_price(sid, 1))

    async def test_cannot_accept_empty(self):
        sid = await self._submission(entries={5: 1})
        await self.db.set_entry(sid, 5, 0)
        self.assertFalse(await self.db.accept(sid, 1))

    async def test_week_filter_uses_message_time_and_name_filter(self):
        a = await self._submission(msg_id=1, created=1_000)     # inside
        b = await self._submission(msg_id=2, created=5_000)     # outside
        await self.db.accept(a, 1)
        await self.db.accept(b, 1)
        rows = await self.db.totals(1, 0, 2_000)
        self.assertEqual(sum(r["kills"] for r in rows), 7)
        self.assertEqual(len(await self.db.totals(1, 0, 2_000, target_name="alpha")), 2)
        self.assertEqual(await self.db.totals(1, 0, 2_000, target_name="nope"), [])
        self.assertEqual(len(await self.db.totals(1, 0, 10_000, user_id=5)), 1)

    async def test_settings_and_roles(self):
        await self.db.set_review_channel(1, 555, boundary=123)
        await self.db.set_report_channel(1, 666, boundary=5)
        s = await self.db.get_settings(1)
        self.assertEqual((s["review_channel_id"], s["report_channel_id"], s["last_report_boundary"]), (555, 666, 123))
        await self.db.set_review_channel(1, 777, boundary=999)  # boundary must not move on re-setup
        self.assertEqual((await self.db.get_settings(1))["last_report_boundary"], 123)
        await self.db.add_reviewer_role(1, 42)
        self.assertEqual(await self.db.reviewer_role_ids(1), {42})
        await self.db.remove_reviewer_role(1, 42)
        self.assertEqual(await self.db.reviewer_role_ids(1), set())

    async def test_report_channel_first_sets_boundary(self):
        await self.db.set_report_channel(2, 9, boundary=77)
        self.assertEqual((await self.db.get_settings(2))["last_report_boundary"], 77)
        await self.db.set_last_report_boundary(2, 88)
        self.assertEqual((await self.db.get_settings(2))["last_report_boundary"], 88)

    async def test_delete_submission(self):
        sid = await self._submission(msg_id=3)
        await self.db.delete_submission(sid)
        self.assertIsNone(await self.db.get_submission(sid))
        self.assertIsNotNone(await self._submission(msg_id=3))  # can be submitted again

    async def test_pending_count(self):
        await self._submission(msg_id=1, created=100)
        self.assertEqual(await self.db.pending_count(1, 0, 1000), 1)
        self.assertEqual(await self.db.pending_count(1, 500, 1000), 0)



    # ------------------------------------------------- ??edit on reviewed claims
    async def _accepted(self, price=100_000, entries=None):
        sid = await self._submission(price=price, entries=entries or {5: 5, 6: 2})
        self.assertTrue(await self.db.accept(sid, 1))
        return sid

    async def _by_user(self):
        return {r["user_id"]: (r["kills"], r["amount"]) for r in await self.db.totals(1, 0, 10_000)}

    async def test_edit_accepted_kills_updates_totals(self):
        sid = await self._accepted()
        self.assertTrue(await self.db.set_entry(sid, 5, 3, allow_accepted=True))
        self.assertEqual(await self._by_user(), {5: (3, 300_000), 6: (2, 200_000)})

    async def test_edit_accepted_remove_and_add_member(self):
        sid = await self._accepted()
        self.assertTrue(await self.db.set_entry(sid, 6, 0, allow_accepted=True))
        self.assertTrue(await self.db.set_entry(sid, 7, 4, allow_accepted=True))
        self.assertEqual(await self._by_user(), {5: (5, 500_000), 7: (4, 400_000)})
        self.assertEqual((await self.db.get_submission(sid))["status"], "accepted")

    async def test_edit_accepted_price_reprices_every_member(self):
        sid = await self._accepted(price=100_000)
        self.assertTrue(await self.db.set_price(sid, 250_000, allow_accepted=True))
        self.assertEqual(await self._by_user(), {5: (5, 1_250_000), 6: (2, 500_000)})

    async def test_removing_last_member_deletes_accepted_claim(self):
        sid = await self._accepted(entries={5: 5})
        self.assertTrue(await self.db.set_entry(sid, 5, 0, allow_accepted=True))
        self.assertEqual((await self.db.get_submission(sid))["status"], "deleted")
        self.assertEqual(await self.db.totals(1, 0, 10_000), [])

    async def test_delete_accepted_claim_is_not_counted(self):
        sid = await self._accepted()
        other = await self._submission(msg_id=2, entries={5: 1})
        await self.db.accept(other, 1)
        self.assertTrue(await self.db.delete_claim(sid, deleted_by=42))
        sub = await self.db.get_submission(sid)
        self.assertEqual((sub["status"], sub["deleted_by"]), ("deleted", 42))
        self.assertEqual(await self._by_user(), {5: (1, 50_000)})   # only the other claim is left
        self.assertEqual(await self.db.leaderboard(1, 0, 10_000), [
            {"user_id": 5, "kills": 1, "amount": 50_000}])

    async def test_delete_pending_claim_and_no_double_delete(self):
        sid = await self._submission()
        self.assertTrue(await self.db.delete_claim(sid, 1))
        self.assertFalse(await self.db.delete_claim(sid, 1))
        self.assertFalse(await self.db.accept(sid, 1))               # cannot be accepted afterwards
        self.assertEqual(await self.db.pending_count(1, 0, 10_000), 0)

    async def test_deleted_message_cannot_be_resubmitted(self):
        sid = await self._submission(msg_id=9)
        await self.db.delete_claim(sid, 1)
        self.assertIsNone(await self._submission(msg_id=9))

    async def test_rejected_claim_cannot_be_edited_or_deleted(self):
        sid = await self._submission()
        await self.db.reject(sid, 1, "bad")
        self.assertFalse(await self.db.set_entry(sid, 5, 9, allow_accepted=True))
        self.assertFalse(await self.db.set_price(sid, 1, allow_accepted=True))
        self.assertFalse(await self.db.delete_claim(sid, 1))

if __name__ == "__main__":
    unittest.main()
