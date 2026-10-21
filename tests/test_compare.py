import pandas as pd

from xlagent.compare import Tolerance, compare_files
from xlagent.xlsx_io import write_csv, write_xlsx

EXPECTED = {
    "Summary": pd.DataFrame(
        {"Shop": ["North", "Harbour"], "Units": [10, 20], "Revenue": [100.10, 200.20]}
    ),
    "Notes": pd.DataFrame({"Note": ["a", "b"]}),
}


def _write(tmp_path, name, sheets):
    return write_xlsx(tmp_path / name, sheets)


def test_identical_files_match(tmp_path):
    exp = _write(tmp_path, "e.xlsx", EXPECTED)
    report = compare_files(exp, exp)
    assert report.ok
    assert report.cells_compared == 8


def test_small_float_noise_is_tolerated_and_row_order_ignored(tmp_path):
    exp = _write(tmp_path, "e.xlsx", EXPECTED)
    act_summary = pd.DataFrame(
        {"Revenue": [200.2000001, 100.1], "Units": [20.0, 10.0], "Shop": ["Harbour", "North"]}
    )
    act = _write(tmp_path, "a.xlsx", {"Summary": act_summary, "Notes": EXPECTED["Notes"]})
    assert compare_files(exp, act).ok


def test_value_difference_is_explained(tmp_path):
    exp = _write(tmp_path, "e.xlsx", EXPECTED)
    wrong = EXPECTED["Summary"].assign(Revenue=[100.10, 250.00])
    act = _write(tmp_path, "a.xlsx", {"Summary": wrong, "Notes": EXPECTED["Notes"]})
    report = compare_files(exp, act)
    assert not report.ok
    text = report.summary()
    assert "column 'Revenue': 1 of 2 values differ" in text
    assert "expected 200.2, got 250.0" in text


def test_tolerance_is_configurable(tmp_path):
    exp = _write(tmp_path, "e.xlsx", EXPECTED)
    off = EXPECTED["Summary"].assign(Revenue=[100.14, 200.20])
    act = _write(tmp_path, "a.xlsx", {"Summary": off, "Notes": EXPECTED["Notes"]})
    assert not compare_files(exp, act).ok
    assert compare_files(exp, act, Tolerance(atol=0.05)).ok


def test_shape_and_column_problems(tmp_path):
    exp = _write(tmp_path, "e.xlsx", EXPECTED)
    act = _write(
        tmp_path,
        "a.xlsx",
        {
            "Summary": EXPECTED["Summary"].drop(columns="Units").assign(Extra=1),
            "Notes": pd.DataFrame({"Note": ["a"]}),
            "Debug": pd.DataFrame({"x": [1]}),
        },
    )
    text = compare_files(exp, act).summary()
    assert "missing columns ['Units']" in text
    assert "unexpected extra columns ['Extra']" in text
    assert "expected 2 rows, got 1 rows" in text
    assert "unexpected extra sheets ['Debug']" in text


def test_missing_sheet(tmp_path):
    exp = _write(tmp_path, "e.xlsx", EXPECTED)
    act = _write(tmp_path, "a.xlsx", {"Summary": EXPECTED["Summary"]})
    assert "missing sheet 'Notes'" in compare_files(exp, act).summary()


def test_row_order_can_be_enforced(tmp_path):
    exp = _write(tmp_path, "e.xlsx", {"S": EXPECTED["Summary"]})
    act = _write(tmp_path, "a.xlsx", {"S": EXPECTED["Summary"].iloc[::-1]})
    assert compare_files(exp, act).ok
    assert not compare_files(exp, act, Tolerance(ignore_row_order=False)).ok


def test_csv_vs_csv(tmp_path):
    frame = pd.DataFrame({"Date": ["2026-09-01"], "Name": [" Alex "]})
    exp = write_csv(tmp_path / "e.csv", frame)
    act = write_csv(tmp_path / "result.csv", frame.assign(Name="Alex"))
    assert compare_files(exp, act).ok
