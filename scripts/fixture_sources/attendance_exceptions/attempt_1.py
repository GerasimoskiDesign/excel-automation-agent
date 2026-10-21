import pandas as pd

GRACE_MINUTES = 5
LATE_THRESHOLD = 3


def _find(input_paths, word):
    for path in input_paths:
        if word in path.name.lower():
            return path
    raise ValueError(f"Could not find an input file with '{word}' in its name.")


def _read(path):
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str)
    return pd.read_excel(path, dtype=str)


def _minutes(series):
    times = pd.to_datetime(series.astype(str).str.strip(), format="%H:%M")
    return times.dt.hour * 60 + times.dt.minute


def run(input_paths, output_path):
    events = _read(_find(input_paths, "clock"))
    roster = _read(_find(input_paths, "roster"))
    events.columns = [c.strip() for c in events.columns]
    roster.columns = [c.strip() for c in roster.columns]

    events = events.merge(
        roster[["Employee ID", "Name", "Shift Start"]], on="Employee ID", how="left"
    )
    events["in_min"] = _minutes(events["Clock In"])
    events["out_min"] = _minutes(events["Clock Out"])
    events["shift_min"] = _minutes(events["Shift Start"])

    missing_in = events[events["in_min"].isna() & events["out_min"].notna()]
    swapped = events[events["in_min"].notna() & events["out_min"].notna()]
    swapped = swapped[swapped["out_min"] < swapped["in_min"]]

    # Swapped rows are data errors and must not count as late arrivals.
    valid = events[events["in_min"].notna() & ~events.index.isin(swapped.index)]
    late = valid[valid["in_min"] > valid["shift_min"] + GRACE_MINUTES]
    late_summary = (
        late.sort_values("Date")
        .groupby(["Employee ID", "Name"])
        .agg(**{"Late Days": ("Date", "count"), "Late Dates": ("Date", ", ".join)})
        .reset_index()
    )
    repeated = late_summary[late_summary["Late Days"] >= LATE_THRESHOLD]

    sheets = {
        "Missing Clock-In": missing_in[["Employee ID", "Name", "Date"]].sort_values(
            ["Employee ID", "Date"]
        ),
        "Repeated Late": repeated.sort_values("Employee ID"),
        "Out Before In": swapped[
            ["Employee ID", "Name", "Date", "Clock In", "Clock Out"]
        ].sort_values(["Employee ID", "Date"]),
    }
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
