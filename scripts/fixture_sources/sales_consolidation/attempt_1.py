import os
import re

import pandas as pd

# The shops' tills export different header names; map them to one vocabulary.
HEADER_ALIASES = {
    "date": "Date",
    "sale date": "Date",
    "invoice no": "Invoice No",
    "invoice": "Invoice No",
    "product": "Product",
    "item": "Product",
    "qty": "Qty",
    "quantity": "Qty",
    "unit price": "Unit Price",
    "price": "Unit Price",
    "line total": "Line Total",
    "amount": "Line Total",
}
REQUIRED = ["Date", "Invoice No", "Product", "Qty", "Line Total"]
FILE_NAME = re.compile(r"sales_(?P<shop>[A-Za-z]+)_(?P<month>\d{4}-\d{2})")
REFUND = re.compile(r"-R\d+$")


def _read(path):
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path)


def _load_shop_file(path):
    file_stem = os.path.splitext(os.path.basename(str(path)))[0]
    match = FILE_NAME.search(file_stem)
    if match is None:
        raise ValueError(f"{path.name}: expected a file name like sales_<shop>_<YYYY-MM>.xlsx")
    frame = _read(path)
    frame = frame.rename(
        columns=lambda c: HEADER_ALIASES.get(str(c).strip().lower(), str(c).strip())
    )
    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")

    # Blank rows (North) and the TOTAL row (Harbour) have no invoice number.
    frame = frame.dropna(how="all")
    frame = frame[frame["Invoice No"].notna()]
    frame = frame[frame["Product"].astype(str).str.strip().str.upper() != "TOTAL"].copy()

    # Station exports dates as text in day.month.year format.
    if not pd.api.types.is_datetime64_any_dtype(frame["Date"]):
        frame["Date"] = pd.to_datetime(frame["Date"].astype(str).str.strip(), format="%d.%m.%Y")

    frame["Qty"] = pd.to_numeric(frame["Qty"])
    frame["Line Total"] = pd.to_numeric(frame["Line Total"])
    frame["Invoice No"] = frame["Invoice No"].astype(str).str.strip()
    frame["Shop"] = match["shop"].title()
    frame["Month"] = frame["Date"].dt.strftime("%Y-%m")
    # Refunds reduce units and revenue but are not counted as invoices.
    frame["Is Refund"] = frame["Invoice No"].str.contains(REFUND)
    return frame[["Shop", "Month", "Invoice No", "Qty", "Line Total", "Is Refund"]]


def _aggregate(lines, keys):
    invoices = lines[~lines["Is Refund"]].groupby(keys)["Invoice No"].nunique()
    grouped = lines.groupby(keys).agg(Units=("Qty", "sum"), Revenue=("Line Total", "sum"))
    grouped.insert(0, "Invoices", invoices)
    grouped["Invoices"] = grouped["Invoices"].fillna(0).astype(int)
    grouped["Units"] = grouped["Units"].astype(int)
    grouped["Revenue"] = grouped["Revenue"].round(2)
    return grouped.reset_index()


def run(input_paths, output_path):
    lines = pd.concat([_load_shop_file(p) for p in input_paths], ignore_index=True)

    summary = _aggregate(lines, ["Shop", "Month"]).sort_values(["Shop", "Month"])
    totals = _aggregate(lines, ["Shop"]).sort_values("Shop")
    grand_total = pd.DataFrame(
        [
            {
                "Shop": "All shops",
                "Invoices": int(totals["Invoices"].sum()),
                "Units": int(totals["Units"].sum()),
                "Revenue": round(float(lines["Line Total"].sum()), 2),
            }
        ]
    )
    totals = pd.concat([totals, grand_total], ignore_index=True)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        totals.to_excel(writer, sheet_name="Shop Totals", index=False)
