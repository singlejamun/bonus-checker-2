import io
import unittest

from openpyxl import load_workbook

from bot.sheet import build_workbook

A, B = 111, 222


class SheetTests(unittest.TestCase):
    def make(self):
        rows = [
            {"target_name": "Informal", "user_id": A, "kills": 10, "amount": 500_000},
            {"target_name": "Foundry", "user_id": A, "kills": 2, "amount": 60_000},
            {"target_name": "Informal", "user_id": B, "kills": 3, "amount": 150_000},
        ]
        data = build_workbook(rows, ["Informal", "Foundry", "Empty"], {"informal": 50_000}, {A: "Rahul"}, "window")
        return load_workbook(io.BytesIO(data))["Bonus"]

    def test_headers_and_order(self):
        ws = self.make()
        headers = [c.value for c in ws[1][:12]]
        self.assertEqual(headers[:7], ["Name", "Informal", "Informal Total", "Foundry", "Foundry Total", "Empty", "Empty Total"])
        self.assertEqual(headers[7:12], ["Subtotal", "Warning", "Cut %", "Cut Amount", "Grand Total"])
        self.assertEqual(ws["A2"].value, "Rahul")       # most kills first
        self.assertEqual(ws["A3"].value, "ID 222")      # unknown name falls back to the id

    def test_values(self):
        ws = self.make()
        self.assertEqual((ws["B2"].value, ws["C2"].value), (10, 500_000))
        self.assertEqual((ws["D2"].value, ws["E2"].value), (2, 60_000))
        self.assertEqual(ws["H2"].value, "=C2+E2+G2")

    def test_warning_cut_formulas_and_prefill(self):
        rows = [{"target_name": "Informal", "user_id": A, "kills": 10, "amount": 500_000}]
        data = build_workbook(rows, ["Informal"], {}, {A: "Rahul"}, "w", warnings={A: 2})
        ws = load_workbook(io.BytesIO(data))["Bonus"]
        self.assertEqual(ws["E2"].value, 2)          # Warning pre-filled
        self.assertIn("INDEX", ws["F2"].value)       # Cut %
        self.assertEqual(ws["H2"].value, "=D2-G2")   # Grand Total = Subtotal - Cut Amount

    def test_empty_week(self):
        data = build_workbook([], ["Informal"], {}, {}, "w")
        ws = load_workbook(io.BytesIO(data))["Bonus"]
        self.assertEqual(ws["A1"].value, "Name")


if __name__ == "__main__":
    unittest.main()
