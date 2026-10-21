"""Seeded generator for the three synthetic example tasks.

Each example folder gets:
  task.md      - the request as a non-technical colleague would write it
  inputs/      - the messy sample files they would upload
  expected/    - the output they expect, built by an independent reference
                 implementation in this module

Why the reference implementation lives here and not in the fixtures: the
replayed "model" scripts are graded against these expected files, so the two
must be written independently or the eval would only prove the code agrees
with itself.
"""

from __future__ import annotations

import logging
import shutil
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from xlagent.xlsx_io import write_csv, write_xlsx

log = logging.getLogger(__name__)

EXAMPLE_NAMES = ("sales_consolidation", "asset_depreciation", "attendance_exceptions")

# ---------------------------------------------------------------------------
# 1. Sales consolidation
# ---------------------------------------------------------------------------

SHOPS = ("north", "harbour", "station")
SALES_MONTHS = ("2026-07", "2026-08", "2026-09")
PRODUCTS = {
    "USB-C Cable": 9.90,
    "Phone Case": 14.50,
    "Screen Protector": 7.99,
    "Power Bank": 29.00,
    "Wireless Earbuds": 49.90,
    "Wall Charger 20W": 19.90,
}
STATION_HEADERS = {
    "Date": "Sale Date",
    "Invoice No": "Invoice",
    "Product": "Item",
    "Qty": "Quantity",
    "Unit Price": "Price",
    "Line Total": "Amount",
}

SALES_TASK = """\
Hi! Every month I get one Excel file per shop (North, Harbour, Station) with all the
sales lines, and I copy-paste them into one summary by hand. It takes me most of a morning.

What I need: one workbook `sales_summary.xlsx` with
- a sheet "Summary": one row per shop and month with Shop, Month (like 2026-07),
  Invoices (how many invoices), Units and Revenue
- a sheet "Shop Totals": one row per shop with Invoices, Units, Revenue, and a last row
  "All shops" with the grand total.

Things I know about the files:
- The shop is in the file name, e.g. sales_harbour_2026-08.xlsx.
- Harbour adds a TOTAL row at the bottom of every file - that must not be counted twice.
- Station's till exports different column names and the dates as text (day.month.year).
- North's files sometimes have empty rows in the middle.
- Refunds have an R in the invoice number and a negative quantity. They are not invoices,
  but they must reduce units and revenue.
I attached three months of files and last quarter's summary as I would do it by hand.
"""


def _month_days(month: str) -> list[date]:
    start = date.fromisoformat(f"{month}-01")
    days = []
    d = start
    while d.month == start.month:
        days.append(d)
        d += timedelta(days=1)
    return days


def _sales_lines(rng: np.random.Generator, shop: str, month: str) -> pd.DataFrame:
    code = shop[0].upper()
    yymm = month[2:4] + month[5:7]
    days = _month_days(month)
    names = list(PRODUCTS)
    rows = []
    n_invoices = int(rng.integers(12, 20))
    for i in range(1, n_invoices + 1):
        day = days[int(rng.integers(0, len(days)))]
        for _ in range(int(rng.integers(1, 4))):
            product = names[int(rng.integers(0, len(names)))]
            qty = int(rng.integers(1, 4))
            rows.append((day, f"{code}-{yymm}-{i:04d}", product, qty, PRODUCTS[product]))
    n_refunds = int(rng.integers(1, 3))
    for r in range(1, n_refunds + 1):
        day = days[int(rng.integers(0, len(days)))]
        product = names[int(rng.integers(0, len(names)))]
        rows.append((day, f"{code}-{yymm}-R{r:03d}", product, -1, PRODUCTS[product]))
    frame = pd.DataFrame(rows, columns=["Date", "Invoice No", "Product", "Qty", "Unit Price"])
    frame["Line Total"] = (frame["Qty"] * frame["Unit Price"]).round(2)
    frame = frame.sort_values(["Date", "Invoice No"], kind="stable").reset_index(drop=True)
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame


def _messy_sales_file(rng: np.random.Generator, shop: str, clean: pd.DataFrame) -> pd.DataFrame:
    """Apply the per-shop export quirks the colleague described."""
    frame = clean.copy()
    if shop == "north":
        empty = pd.DataFrame([[None] * frame.shape[1]] * 2, columns=frame.columns)
        cut = int(rng.integers(3, len(frame) - 3))
        frame = pd.concat([frame.iloc[:cut], empty.iloc[:1], frame.iloc[cut:]])
        cut2 = int(rng.integers(cut + 2, len(frame)))
        frame = pd.concat([frame.iloc[:cut2], empty.iloc[1:], frame.iloc[cut2:]])
        frame = frame.reset_index(drop=True)
    elif shop == "harbour":
        total = {c: None for c in frame.columns}
        total["Product"] = "TOTAL"
        total["Qty"] = int(frame["Qty"].sum())
        total["Line Total"] = round(float(frame["Line Total"].sum()), 2)
        frame = pd.concat([frame, pd.DataFrame([total])], ignore_index=True)
    elif shop == "station":
        frame["Date"] = frame["Date"].dt.strftime("%d.%m.%Y")
        frame = frame.rename(columns=STATION_HEADERS)
    return frame


def reference_sales_summary(clean_by_file: dict[tuple[str, str], pd.DataFrame]) -> dict:
    """Independent reference implementation of the expected sales summary."""
    parts = []
    for (shop, _month), frame in clean_by_file.items():
        part = frame.copy()
        part["Shop"] = shop.title()
        parts.append(part)
    lines = pd.concat(parts, ignore_index=True)
    lines["Month"] = lines["Date"].dt.strftime("%Y-%m")
    lines["IsRefund"] = lines["Invoice No"].str.contains("-R")
    sales_invoices = lines[~lines["IsRefund"]]

    def _agg(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
        inv = sales_invoices.groupby(keys)["Invoice No"].nunique().rename("Invoices")
        units = frame.groupby(keys)["Qty"].sum().rename("Units")
        revenue = frame.groupby(keys)["Line Total"].sum().round(2).rename("Revenue")
        return pd.concat([inv, units, revenue], axis=1).reset_index()

    summary = _agg(lines, ["Shop", "Month"]).sort_values(["Shop", "Month"])
    totals = _agg(lines, ["Shop"]).sort_values("Shop")
    grand = pd.DataFrame(
        [
            {
                "Shop": "All shops",
                "Invoices": int(totals["Invoices"].sum()),
                "Units": int(totals["Units"].sum()),
                "Revenue": round(float(lines["Line Total"].sum()), 2),
            }
        ]
    )
    totals = pd.concat([totals, grand], ignore_index=True)
    return {
        "Summary": summary.reset_index(drop=True),
        "Shop Totals": totals.reset_index(drop=True),
    }


def build_sales(rng: np.random.Generator, folder: Path) -> None:
    clean: dict[tuple[str, str], pd.DataFrame] = {}
    for shop in SHOPS:
        for month in SALES_MONTHS:
            frame = _sales_lines(rng, shop, month)
            clean[(shop, month)] = frame
            messy = _messy_sales_file(rng, shop, frame)
            write_xlsx(folder / "inputs" / f"sales_{shop}_{month}.xlsx", {"Sales": messy})
    write_xlsx(folder / "expected" / "sales_summary.xlsx", reference_sales_summary(clean))
    (folder / "task.md").write_text(SALES_TASK, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# 2. Straight-line depreciation schedule
# ---------------------------------------------------------------------------

CATEGORIES = {
    # category: (useful life in months, cost range, salvage share)
    "Laptop": (36, (900, 2400), 0.0),
    "Server": (60, (4000, 15000), 0.05),
    "Vehicle": (60, (18000, 42000), 0.15),
    "Furniture": (84, (300, 2500), 0.0),
    "Printer": (48, (400, 3500), 0.0),
}
DEPRECIATION_YEAR = 2026

DEPRECIATION_TASK = """\
Every month I update our fixed-asset depreciation schedule in Excel by hand and it is easy
to make mistakes. Attached is the asset register (asset_register.xlsx).

Please produce `depreciation_2026.xlsx` with
- sheet "Schedule": one row per asset and month of 2026 while the asset is on our books:
  Asset ID, Month (2026-01 ... 2026-12), Opening NBV, Depreciation, Closing NBV
- sheet "Category Totals": Category, Assets (how many assets appear in the schedule),
  Depreciation 2026 (total for the year)

Our policy:
- Straight-line: (Cost - Salvage Value) / Useful Life (months), rounded to cents.
- Depreciation starts in the month AFTER the acquisition month. In the acquisition month
  the asset is on the books but has zero depreciation.
- The last month takes the rounding difference so the asset ends exactly at its salvage value,
  and it never goes below salvage value.
- If an asset was disposed, it is depreciated up to and including the disposal month and does
  not appear after that.
- Assets that are already fully depreciated still appear with zero depreciation.
"""


def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _register(rng: np.random.Generator, n_assets: int = 30) -> pd.DataFrame:
    cats = list(CATEGORIES)
    start = date(2021, 6, 1)
    span = (date(2026, 9, 30) - start).days
    rows = []
    for i in range(1, n_assets + 1):
        cat = cats[int(rng.integers(0, len(cats)))]
        life, (lo, hi), salvage_share = CATEGORIES[cat]
        cost = round(float(rng.uniform(lo, hi)), 2)
        acquired = start + timedelta(days=int(rng.integers(0, span)))
        rows.append(
            {
                "Asset ID": f"FA-{i:04d}",
                "Description": f"{cat} #{i}",
                "Category": cat,
                "Acquisition Date": pd.Timestamp(acquired),
                "Cost": cost,
                "Salvage Value": round(cost * salvage_share, 2),
                "Useful Life (months)": life,
                "Disposal Date": pd.NaT,
            }
        )
    frame = pd.DataFrame(rows)
    # Three disposals during 2026 among assets bought before 2026.
    older = frame.index[frame["Acquisition Date"] < pd.Timestamp("2025-06-01")].tolist()
    picks = rng.choice(older, size=3, replace=False)
    for idx, month in zip(sorted(picks), (3, 6, 9), strict=True):
        frame.loc[idx, "Disposal Date"] = pd.Timestamp(date(DEPRECIATION_YEAR, month, 15))
    return frame


def reference_depreciation(register: pd.DataFrame, year: int = DEPRECIATION_YEAR) -> dict:
    """Independent reference implementation of the depreciation policy in the task."""
    rows = []
    for asset in register.itertuples(index=False):
        cost = float(asset.Cost)
        salvage = float(asset[5])
        life = int(asset[6])
        acquired = asset[3].date()
        disposed = None if pd.isna(asset[7]) else asset[7].date()
        base = round(cost - salvage, 2)
        monthly = round(base / life, 2)
        start_idx = _month_index(acquired) + 1

        def accumulated(n_done: int, base=base, monthly=monthly, life=life) -> float:
            return base if n_done >= life else round(monthly * n_done, 2)

        for month in range(1, 13):
            m_idx = year * 12 + month - 1
            if m_idx < _month_index(acquired):
                continue
            if disposed is not None and m_idx > _month_index(disposed):
                continue
            done_before = max(0, m_idx - start_idx)
            done_after = max(0, m_idx - start_idx + 1)
            opening = round(cost - accumulated(done_before), 2)
            closing = round(cost - accumulated(done_after), 2)
            rows.append(
                {
                    "Asset ID": asset[0],
                    "Category": asset[2],
                    "Month": f"{year}-{month:02d}",
                    "Opening NBV": opening,
                    "Depreciation": round(opening - closing, 2),
                    "Closing NBV": closing,
                }
            )
    sched = pd.DataFrame(rows).sort_values(["Asset ID", "Month"]).reset_index(drop=True)
    totals = (
        sched.groupby("Category")
        .agg(**{"Assets": ("Asset ID", "nunique"), "Depreciation 2026": ("Depreciation", "sum")})
        .reset_index()
    )
    totals["Depreciation 2026"] = totals["Depreciation 2026"].round(2)
    return {"Schedule": sched.drop(columns="Category"), "Category Totals": totals}


def build_depreciation(rng: np.random.Generator, folder: Path) -> None:
    register = _register(rng)
    write_xlsx(folder / "inputs" / "asset_register.xlsx", {"Register": register})
    write_xlsx(folder / "expected" / "depreciation_2026.xlsx", reference_depreciation(register))
    (folder / "task.md").write_text(DEPRECIATION_TASK, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# 3. Attendance exceptions
# ---------------------------------------------------------------------------

FIRST = ("Alex", "Sam", "Jordan", "Taylor", "Robin", "Casey", "Morgan", "Jamie", "Riley", "Drew")
LAST = ("Brook", "Hill", "Stone", "Field", "Lake", "Wood", "Marsh", "Vale", "Glen", "Ford")
TEAMS = {"Warehouse": "07:00", "Front Desk": "08:30", "Support": "09:00"}
ATTENDANCE_MONTH = "2026-09"
GRACE_MINUTES = 5
LATE_THRESHOLD = 3
MISSING = "-"

ATTENDANCE_TASK = """\
At the end of each month I go through the time-clock export line by line to find problems
for HR. Attached: the September export (clock_events_2026-09.csv) and our roster with
everyone's shift start (roster.xlsx).

I need `attendance_exceptions.xlsx` with three sheets:
- "Missing Clock-In": Employee ID, Name, Date - days where there is a clock-out but no clock-in
  (the time clock writes "-" when a punch is missing)
- "Repeated Late": Employee ID, Name, Late Days, Late Dates - everyone who was late 3 or more
  times in the month. Late means clocking in more than 5 minutes after their shift start.
  Late Dates is a comma-separated list like "2026-09-03, 2026-09-10".
- "Out Before In": Employee ID, Name, Date, Clock In, Clock Out - rows where the clock-out
  time is earlier than the clock-in time (somebody typed the times the wrong way round).
  These rows are data errors, so please do not count them as late.
Everything sorted by Employee ID and date. Days without any row are absences, HR has those
already, so ignore them.
"""


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _attendance(rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    teams = list(TEAMS)
    roster_rows = []
    for i in range(1, 21):
        team = teams[(i - 1) % len(teams)]
        name = f"{FIRST[int(rng.integers(0, len(FIRST)))]} {LAST[int(rng.integers(0, len(LAST)))]}"
        roster_rows.append(
            {"Employee ID": f"E{i:03d}", "Name": name, "Team": team, "Shift Start": TEAMS[team]}
        )
    roster = pd.DataFrame(roster_rows)
    often_late = set(rng.choice(roster["Employee ID"].to_numpy(), size=5, replace=False))
    workdays = [d for d in _month_days(ATTENDANCE_MONTH) if d.weekday() < 5]
    events = []
    for emp in roster.itertuples(index=False):
        start = _to_minutes(emp[3])
        late_p = 0.22 if emp[0] in often_late else 0.03
        for day in workdays:
            if rng.random() < 0.04:
                continue  # absence: no row at all
            if rng.random() < late_p:
                clock_in = start + int(rng.integers(GRACE_MINUTES + 1, 45))
            else:
                clock_in = start + int(np.clip(rng.normal(-6, 3), -20, GRACE_MINUTES))
            clock_out = clock_in + 8 * 60 + int(rng.integers(-10, 25))
            cin, cout = _hhmm(clock_in), _hhmm(clock_out)
            if rng.random() < 0.025:
                cin = MISSING
            events.append([emp[0], day.isoformat(), cin, cout])
    frame = pd.DataFrame(events, columns=["Employee ID", "Date", "Clock In", "Clock Out"])
    # Three swapped punches (clock-out typed before clock-in).
    candidates = frame.index[frame["Clock In"] != MISSING].tolist()
    for idx in rng.choice(candidates, size=3, replace=False):
        cin, cout = frame.loc[idx, "Clock In"], frame.loc[idx, "Clock Out"]
        frame.loc[idx, "Clock In"], frame.loc[idx, "Clock Out"] = cout, cin
    return roster, frame


def reference_attendance(roster: pd.DataFrame, events: pd.DataFrame) -> dict:
    """Independent reference implementation of the attendance exception rules."""
    names = dict(zip(roster["Employee ID"], roster["Name"], strict=True))
    shift = dict(zip(roster["Employee ID"], roster["Shift Start"], strict=True))
    missing, swapped, late = [], [], {}
    for row in events.itertuples(index=False):
        emp, day, cin, cout = row
        if cin == MISSING:
            if cout != MISSING:
                missing.append({"Employee ID": emp, "Name": names[emp], "Date": day})
            continue
        if cout != MISSING and _to_minutes(cout) < _to_minutes(cin):
            swapped.append(
                {
                    "Employee ID": emp,
                    "Name": names[emp],
                    "Date": day,
                    "Clock In": cin,
                    "Clock Out": cout,
                }
            )
            continue
        if _to_minutes(cin) > _to_minutes(shift[emp]) + GRACE_MINUTES:
            late.setdefault(emp, []).append(day)
    repeated = [
        {
            "Employee ID": emp,
            "Name": names[emp],
            "Late Days": len(days),
            "Late Dates": ", ".join(sorted(days)),
        }
        for emp, days in sorted(late.items())
        if len(days) >= LATE_THRESHOLD
    ]
    cols_m = ["Employee ID", "Name", "Date"]
    cols_s = ["Employee ID", "Name", "Date", "Clock In", "Clock Out"]
    cols_r = ["Employee ID", "Name", "Late Days", "Late Dates"]
    return {
        "Missing Clock-In": pd.DataFrame(missing, columns=cols_m).sort_values(cols_m[::2]),
        "Repeated Late": pd.DataFrame(repeated, columns=cols_r),
        "Out Before In": pd.DataFrame(swapped, columns=cols_s).sort_values(["Employee ID", "Date"]),
    }


def build_attendance(rng: np.random.Generator, folder: Path) -> None:
    roster, events = _attendance(rng)
    write_csv(folder / "inputs" / f"clock_events_{ATTENDANCE_MONTH}.csv", events)
    write_xlsx(folder / "inputs" / "roster.xlsx", {"Roster": roster})
    expected = {
        k: v.reset_index(drop=True) for k, v in reference_attendance(roster, events).items()
    }
    write_xlsx(folder / "expected" / "attendance_exceptions.xlsx", expected)
    (folder / "task.md").write_text(ATTENDANCE_TASK, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------


BUILDERS = {
    "sales_consolidation": (build_sales, 0),
    "asset_depreciation": (build_depreciation, 1),
    "attendance_exceptions": (build_attendance, 2),
}


def generate_examples(out_dir: Path, seed: int = 42) -> list[Path]:
    """(Re)generate all example folders. Each example gets its own derived seed so adding
    rows to one example never shifts the random stream of another."""
    out_dir = Path(out_dir)
    written = []
    for name, (builder, offset) in BUILDERS.items():
        folder = out_dir / name
        for sub in ("inputs", "expected"):
            if (folder / sub).exists():
                shutil.rmtree(folder / sub)
        rng = np.random.default_rng(seed + offset)
        builder(rng, folder)
        log.info("generated example %s", folder)
        written.append(folder)
    return written
