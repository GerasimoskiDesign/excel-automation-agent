import hashlib

import pandas as pd

from xlagent.evaluation import run_eval
from xlagent.examples_gen import EXAMPLE_NAMES, generate_examples
from xlagent.llm.replay import ReplayProvider
from xlagent.xlsx_io import read_tables


def _digest(folder):
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_eval_passes_in_replay_mode(examples_dir, tmp_path):
    rows = run_eval(ReplayProvider(), examples_dir, work_dir=tmp_path)
    assert [r.example for r in rows] == list(EXAMPLE_NAMES)
    assert all(r.passed for r in rows), rows
    # Each example exercises a different repair path before passing.
    assert {r.example: r.outcomes[0] for r in rows} == {
        "sales_consolidation": "safety_rejected",
        "asset_depreciation": "mismatch",
        "attendance_exceptions": "crashed",
    }
    assert all((tmp_path / f"{r.example}.zip").exists() for r in rows)


def test_examples_are_reproducible_byte_for_byte(examples_dir, tmp_path):
    generate_examples(tmp_path, seed=42)
    for name in EXAMPLE_NAMES:
        assert _digest(tmp_path / name) == _digest(examples_dir / name), name


def test_different_seed_changes_data(tmp_path):
    generate_examples(tmp_path / "a", seed=1)
    generate_examples(tmp_path / "b", seed=2)
    a = _digest(tmp_path / "a" / "sales_consolidation")
    b = _digest(tmp_path / "b" / "sales_consolidation")
    assert a != b


def test_sales_example_contains_the_described_quirks(examples_dir):
    inputs = examples_dir / "sales_consolidation" / "inputs"
    harbour = next(iter(read_tables(sorted(inputs.glob("sales_harbour_*"))[0]).values()))
    north = next(iter(read_tables(sorted(inputs.glob("sales_north_*"))[0]).values()))
    station = next(iter(read_tables(sorted(inputs.glob("sales_station_*"))[0]).values()))
    assert harbour["Product"].iloc[-1] == "TOTAL"
    assert north.isna().all(axis=1).sum() == 2
    assert "Sale Date" in station.columns and station["Sale Date"].str.match(r"\d\d\.\d\d").all()
    all_invoices = pd.concat([harbour["Invoice No"], north["Invoice No"]]).dropna()
    assert all_invoices.str.contains("-R").any()


def test_sales_expected_totals_are_consistent(examples_dir):
    tables = read_tables(examples_dir / "sales_consolidation" / "expected" / "sales_summary.xlsx")
    summary, totals = tables["Summary"], tables["Shop Totals"]
    grand = totals[totals["Shop"] == "All shops"].iloc[0]
    assert grand["Units"] == summary["Units"].sum()
    assert abs(grand["Revenue"] - summary["Revenue"].sum()) < 0.01


def test_depreciation_never_goes_below_salvage(examples_dir):
    folder = examples_dir / "asset_depreciation"
    register = next(iter(read_tables(folder / "inputs" / "asset_register.xlsx").values()))
    schedule = read_tables(folder / "expected" / "depreciation_2026.xlsx")["Schedule"]
    merged = schedule.merge(register, on="Asset ID")
    assert (merged["Closing NBV"] >= merged["Salvage Value"] - 0.005).all()
    assert (merged["Depreciation"] >= 0).all()
    assert (
        (merged["Opening NBV"] - merged["Depreciation"] - merged["Closing NBV"]).abs() < 0.01
    ).all()
    disposed = register[register["Disposal Date"].notna()]
    for asset in disposed.itertuples():
        months = schedule.loc[schedule["Asset ID"] == asset[1], "Month"]
        assert months.max() == asset[8].strftime("%Y-%m")


def test_attendance_expected_covers_every_exception_type(examples_dir):
    tables = read_tables(
        examples_dir / "attendance_exceptions" / "expected" / "attendance_exceptions.xlsx"
    )
    assert all(len(t) > 0 for t in tables.values())
    assert (tables["Repeated Late"]["Late Days"] >= 3).all()
    out_before_in = tables["Out Before In"]
    assert (out_before_in["Clock Out"] < out_before_in["Clock In"]).all()
