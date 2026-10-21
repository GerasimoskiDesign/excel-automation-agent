import pandas as pd

YEAR = 2026
REQUIRED = [
    "Asset ID",
    "Category",
    "Acquisition Date",
    "Cost",
    "Salvage Value",
    "Useful Life (months)",
]


def _month_number(timestamp):
    return timestamp.year * 12 + timestamp.month - 1


def _accumulated(months_done, depreciable, monthly, life):
    # The last month takes the rounding difference, so after `life` months the
    # accumulated depreciation is exactly the depreciable amount.
    if months_done >= life:
        return depreciable
    return round(monthly * months_done, 2)


def _asset_rows(asset):
    cost = float(asset["Cost"])
    salvage = float(asset["Salvage Value"])
    life = int(asset["Useful Life (months)"])
    acquired = _month_number(asset["Acquisition Date"])
    disposed = asset.get("Disposal Date")
    disposed = None if pd.isna(disposed) else _month_number(disposed)
    depreciable = round(cost - salvage, 2)
    monthly = round(depreciable / life, 2)
    first_month = acquired  # depreciation runs from the acquisition month

    rows = []
    for month in range(1, 13):
        current = YEAR * 12 + month - 1
        if current < acquired:
            continue  # not yet on the books
        if disposed is not None and current > disposed:
            continue  # disposed: depreciated up to and including the disposal month
        done_before = max(0, current - first_month)
        done_after = max(0, current - first_month + 1)
        opening = round(cost - _accumulated(done_before, depreciable, monthly, life), 2)
        closing = round(cost - _accumulated(done_after, depreciable, monthly, life), 2)
        rows.append(
            {
                "Asset ID": asset["Asset ID"],
                "Category": asset["Category"],
                "Month": f"{YEAR}-{month:02d}",
                "Opening NBV": opening,
                "Depreciation": round(opening - closing, 2),
                "Closing NBV": closing,
            }
        )
    return rows


def run(input_paths, output_path):
    register_path = next((p for p in input_paths if "register" in p.name.lower()), None)
    if register_path is None:
        raise ValueError("Could not find the asset register (a file with 'register' in its name).")
    register = pd.read_excel(register_path)
    register.columns = [str(c).strip() for c in register.columns]
    missing = [c for c in REQUIRED if c not in register.columns]
    if missing:
        raise ValueError(f"The asset register is missing columns: {missing}")
    register = register.dropna(subset=["Asset ID"])
    for column in ("Acquisition Date", "Disposal Date"):
        if column in register.columns:
            register[column] = pd.to_datetime(register[column])

    rows = []
    for _, asset in register.iterrows():
        rows.extend(_asset_rows(asset))
    schedule = pd.DataFrame(rows).sort_values(["Asset ID", "Month"]).reset_index(drop=True)

    totals = (
        schedule.groupby("Category")
        .agg(Assets=("Asset ID", "nunique"), Depreciation=("Depreciation", "sum"))
        .reset_index()
        .rename(columns={"Depreciation": "Depreciation 2026"})
    )
    totals["Depreciation 2026"] = totals["Depreciation 2026"].round(2)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        schedule.drop(columns="Category").to_excel(writer, sheet_name="Schedule", index=False)
        totals.to_excel(writer, sheet_name="Category Totals", index=False)
