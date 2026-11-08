import unittest

from bot.parsing import (looks_like_price_list, parse_amount, parse_claim, parse_price_list,
                         resolve_price)

A, B, C = 111111111111111111, 222222222222222222, 333333333333333333


class ParseClaimTests(unittest.TestCase):
    def test_x_and_k_both_mean_kills(self):
        r = parse_claim(f"<@{A}> 5x\n<@{B}> 9k")
        self.assertEqual(r.entries, {A: 5, B: 9})
        self.assertIsNone(r.price)

    def test_nickname_mention_and_separators(self):
        r = parse_claim(f"<@!{A}>: 5K and <@{B}> - 3 x")
        self.assertEqual(r.entries, {A: 5, B: 3})

    def test_x_before_number(self):
        self.assertEqual(parse_claim(f"<@{A}> x7").entries, {A: 7})

    def test_word_kills(self):
        self.assertEqual(parse_claim(f"<@{A}> 4 kills").entries, {A: 4})

    def test_same_member_twice_is_summed(self):
        self.assertEqual(parse_claim(f"<@{A}> 2x <@{A}> 3x").entries, {A: 5})

    def test_no_claim(self):
        self.assertFalse(parse_claim("good game everyone"))
        self.assertFalse(parse_claim(f"<@{A}> nice one"))
        self.assertFalse(parse_claim(""))

    def test_number_glued_to_letters_is_not_a_count(self):
        self.assertFalse(parse_claim(f"<@{A}> 5xp"))

    def test_price_in_message(self):
        r = parse_claim(f"100k\n<@{A}> 5x <@{B}> 2k")
        self.assertEqual(r.entries, {A: 5, B: 2})
        self.assertEqual(r.price, 100_000)

    def test_price_after_members(self):
        r = parse_claim(f"<@{A}> 5x <@{B}> 3x 200k")
        self.assertEqual(r.entries, {A: 5, B: 3})
        self.assertEqual(r.price, 200_000)

    def test_price_million_and_plain(self):
        self.assertEqual(parse_claim(f"<@{A}> 1x 1.5m").price, 1_500_000)
        self.assertEqual(parse_claim(f"<@{A}> 1x 2000000").price, 2_000_000)
        self.assertEqual(parse_claim(f"<@{A}> 1x 1,000,000").price, 1_000_000)

    def test_kill_count_is_not_mistaken_for_price(self):
        self.assertIsNone(parse_claim(f"<@{A}> 5k <@{B}> 9x").price)

    def test_ids_and_urls_not_mistaken_for_price(self):
        r = parse_claim(f"<@{A}> 5x https://cdn.discordapp.com/attachments/123456789/987654321/ss.png <#444444444444444444>")
        self.assertIsNone(r.price)

    def test_multiline_realistic(self):
        r = parse_claim(f"Clan war result 200k\n<@{A}> 5x\n<@{B}> 12k\n<@{C}> 1x")
        self.assertEqual(r.entries, {A: 5, B: 12, C: 1})
        self.assertEqual(r.price, 200_000)


class PriceTests(unittest.TestCase):
    def test_parse_amount(self):
        self.assertEqual(parse_amount("50000"), 50_000)
        self.assertEqual(parse_amount("50k"), 50_000)
        self.assertEqual(parse_amount("1M"), 1_000_000)
        self.assertEqual(parse_amount("1.5m"), 1_500_000)
        self.assertEqual(parse_amount("1,000,000"), 1_000_000)
        self.assertIsNone(parse_amount("abc"))
        self.assertIsNone(parse_amount("0"))
        self.assertIsNone(parse_amount(""))

    def test_price_list(self):
        self.assertEqual(parse_price_list("50000,1000000,2000000"), [50_000, 1_000_000, 2_000_000])
        self.assertEqual(parse_price_list("50k, 100k"), [50_000, 100_000])
        self.assertIsNone(parse_price_list("50k,abc"))
        self.assertTrue(looks_like_price_list("50000"))
        self.assertFalse(looks_like_price_list("alpha"))

    def test_resolve_price(self):
        self.assertEqual(resolve_price(100_000, 50_000, 1), (100_000, "message"))
        self.assertEqual(resolve_price(None, 70_000, 50_000), (70_000, "target"))
        self.assertEqual(resolve_price(None, None, 50_000), (50_000, "default"))


if __name__ == "__main__":
    unittest.main()


class TargetArgsTests(unittest.TestCase):
    def test_variants(self):
        from bot.parsing import split_target_args as f
        self.assertEqual(f("alpha"), ("alpha", None, True))
        self.assertEqual(f("alpha team"), ("alpha team", None, True))
        self.assertEqual(f("alpha 50000"), ("alpha", [50_000], True))
        self.assertEqual(f("alpha team 50k"), ("alpha team", [50_000], True))
        self.assertEqual(f("alpha 50000,1000000,2000000"), ("alpha", [50_000, 1_000_000, 2_000_000], True))
        self.assertEqual(f("alpha 50000, 1000000"), ("alpha", [50_000, 1_000_000], True))
        self.assertEqual(f("team k"), ("team k", None, True))
        self.assertEqual(f("squad 2 50000"), ("squad 2", [50_000], True))
        self.assertFalse(f("alpha 50k,oops")[2])
        self.assertEqual(f(""), ("", None, True))
