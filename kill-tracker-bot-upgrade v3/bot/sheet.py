"""Builds the weekly Excel sheet (one row per member, kills + money per target, grand total, warnings)."""
from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F2A5C")
HEADER_FONT = Font(bold=True, color="FFFFFF")
INPUT_FONT = Font(color="0000FF")
THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
DEFAULT_DEDUCTIONS = (0.10, 0.30, 0.50)  # 1, 2, 3 warnings


def build_workbook(
    rows: list[dict[str, Any]],
    target_names: list[str],
    rates: dict[str, int],
    names: dict[int, str],
    window_text: str = "",
    warnings: dict[int, int] | None = None,
) -> bytes:
    """rows: dicts with target_name, user_id, kills, amount (one per user+target).

    warnings: optional {user_id: number of warnings} to pre-fill the Warning column.
    Returns the .xlsx file as bytes.
    """
    warnings = warnings or {}
    # Targets: configured ones first (keeps the column order stable), then any extra seen in the data.
    targets: list[str] = []
    seen: set[str] = set()
    for n in list(target_names) + sorted({r["target_name"] for r in rows}, key=str.lower):
        if n.lower() not in seen:
            seen.add(n.lower())
            targets.append(n)

    per_user: dict[int, dict[str, tuple[int, int]]] = {}
    for r in rows:
        d = per_user.setdefault(int(r["user_id"]), {})
        k, a = d.get(r["target_name"].lower(), (0, 0))
        d[r["target_name"].lower()] = (k + int(r["kills"]), a + int(r["amount"]))

    def total_kills(uid: int) -> int:
        return sum(k for k, _ in per_user[uid].values())

    order = sorted(per_user, key=lambda u: (-total_kills(u), names.get(u, str(u)).lower()))

    wb = Workbook()
    ws = wb.active
    ws.title = "Bonus"

    # ---- header
    headers = ["Name"]
    for t in targets:
        headers += [t, f"{t} Total"]
    headers += ["Subtotal", "Warning", "Cut %", "Cut Amount", "Grand Total"]
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32

    n_t = len(targets)
    sub_col = 2 + 2 * n_t
    warn_col = sub_col + 1
    pct_col = sub_col + 2
    cut_col = sub_col + 3
    grand_col = sub_col + 4
    total_cols = [3 + 2 * i for i in range(n_t)]  # the "<target> Total" columns

    # ---- side tables (rate per kill, warnings) placed two columns after the warning column
    side = grand_col + 2
    ws.cell(row=1, column=side, value="Rate per kill")
    ws.cell(row=1, column=side + 1, value="Amount")
    for c in (side, side + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, BORDER
        cell.alignment = Alignment(horizontal="center")
    for i, t in enumerate(targets, start=2):
        ws.cell(row=i, column=side, value=t).border = BORDER
        a = ws.cell(row=i, column=side + 1, value=int(rates.get(t.lower(), 0)))
        a.font, a.number_format, a.border = INPUT_FONT, "#,##0", BORDER

    w_row = max(n_t + 4, 6)
    ws.cell(row=w_row, column=side, value="Warnings")
    ws.cell(row=w_row, column=side + 1, value="Deduction")
    for c in (side, side + 1):
        cell = ws.cell(row=w_row, column=c)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, BORDER
        cell.alignment = Alignment(horizontal="center")
    for i, pct in enumerate(DEFAULT_DEDUCTIONS, start=1):
        ws.cell(row=w_row + i, column=side, value=f"{i} warning" + ("s" if i > 1 else "")).border = BORDER
        d = ws.cell(row=w_row + i, column=side + 1, value=pct)
        d.font, d.number_format, d.border = INPUT_FONT, "0%", BORDER
    ded_range = f"${get_column_letter(side + 1)}${w_row + 1}:${get_column_letter(side + 1)}${w_row + 3}"

    notes = [
        "Notes",
        "Blue = editable inputs.",
        window_text,
        "Kills and money come from approved claims. Money = kills x the price used on each claim.",
        "Subtotal = sum of the target totals. Warning = number of warnings (1, 2 or 3).",
        "Cut % comes from the Warnings table; Cut Amount = Subtotal x Cut %. Grand Total = Subtotal - Cut Amount.",
        "Change the percentages in the Warnings table (blue) and every row updates.",
    ]
    for i, text in enumerate(notes):
        c = ws.cell(row=w_row + 6 + i, column=side, value=text)
        if i == 0:
            c.font = Font(bold=True)
        else:
            c.font = Font(italic=True, size=9)

    # ---- body
    for r_i, uid in enumerate(order, start=2):
        ws.cell(row=r_i, column=1, value=names.get(uid, f"ID {uid}")).border = BORDER
        for i, t in enumerate(targets):
            k, a = per_user[uid].get(t.lower(), (0, 0))
            kc = ws.cell(row=r_i, column=2 + 2 * i, value=k or None)
            kc.font, kc.border = INPUT_FONT, BORDER
            tc = ws.cell(row=r_i, column=3 + 2 * i, value=a)
            tc.number_format, tc.border = "#,##0", BORDER
        sum_expr = "+".join(f"{get_column_letter(c)}{r_i}" for c in total_cols) or "0"
        wl = f"{get_column_letter(warn_col)}{r_i}"
        sub = ws.cell(row=r_i, column=sub_col, value=f"={sum_expr}")
        sub.number_format, sub.border = "#,##0", BORDER
        w = ws.cell(row=r_i, column=warn_col, value=warnings.get(uid) or None)
        w.font, w.border = INPUT_FONT, BORDER
        pc = ws.cell(
            row=r_i, column=pct_col,
            value=f"=IF(N({wl})<=0,0,INDEX({ded_range},MIN(3,N({wl}))))",
        )
        pc.number_format, pc.border = "0%", BORDER
        sl, pl = f"{get_column_letter(sub_col)}{r_i}", f"{get_column_letter(pct_col)}{r_i}"
        ca = ws.cell(row=r_i, column=cut_col, value=f"=ROUND({sl}*{pl},0)")
        ca.number_format, ca.border = "#,##0", BORDER
        cl = f"{get_column_letter(cut_col)}{r_i}"
        g = ws.cell(row=r_i, column=grand_col, value=f"={sl}-{cl}")
        g.number_format, g.border, g.font = "#,##0", BORDER, Font(bold=True)

    # ---- totals row
    last = 1 + len(order)
    if order:
        tr = last + 1
        ws.cell(row=tr, column=1, value="TOTAL").font = Font(bold=True)
        for c in range(2, grand_col + 1):
            if c in (warn_col, pct_col):
                continue
            col = get_column_letter(c)
            cell = ws.cell(row=tr, column=c, value=f"=SUM({col}2:{col}{last})")
            cell.font = Font(bold=True)
            if c in total_cols or c in (sub_col, cut_col, grand_col):
                cell.number_format = "#,##0"

    # ---- cosmetics
    ws.column_dimensions["A"].width = 26
    for c in range(2, grand_col + 1):
        ws.column_dimensions[get_column_letter(c)].width = 14
    ws.column_dimensions[get_column_letter(side)].width = 18
    ws.column_dimensions[get_column_letter(side + 1)].width = 14
    ws.freeze_panes = "B2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
