"""Compare a produced output file with the expected example the colleague gave us.

Why a tolerant, explained comparison: the diff is fed back to the model as a
repair hint, so it must say *what* is wrong in plain terms ("sheet Summary,
column Revenue: 3 of 9 values differ, e.g. row 2 expected 1018.55 got
1032.10"), not just "files differ". Numeric tolerance avoids failing on
floating-point noise, and row order is ignored by default because most
people do not care about it unless they asked for a sort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from xlagent.xlsx_io import read_tables

MAX_EXAMPLES = 3


@dataclass(frozen=True)
class Tolerance:
    atol: float = 0.01
    rtol: float = 1e-6
    ignore_row_order: bool = True
    ignore_column_order: bool = True


@dataclass
class DiffReport:
    problems: list[str] = field(default_factory=list)
    sheets_compared: int = 0
    cells_compared: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        if self.ok:
            return f"match ({self.sheets_compared} sheets, {self.cells_compared} cells compared)"
        return "\n".join(f"- {p}" for p in self.problems)


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """Make values comparable across xlsx/csv round-trips.

    Dates become ISO strings, whole-number floats become ints, strings are
    stripped, and empty strings count as missing.
    """
    out = frame.copy()
    out.columns = [str(c).strip() for c in out.columns]
    for col in out.columns:
        s = out[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            out[col] = s.dt.strftime("%Y-%m-%d").where(s.notna(), None)
        elif pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            out[col] = s.astype(float)
        else:
            out[col] = s.map(lambda v: v.strip() if isinstance(v, str) else v).replace("", None)
            out[col] = out[col].astype(object).where(out[col].notna(), None)
    return out


def _fmt(value: object) -> str:
    if isinstance(value, np.generic):
        value = value.item()
    return repr(value)


def _sort(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.reset_index(drop=True)
    key = frame.astype(str)
    order = key.sort_values(list(key.columns), kind="stable").index
    return frame.loc[order].reset_index(drop=True)


def _values_equal(expected: pd.Series, actual: pd.Series, tol: Tolerance) -> np.ndarray:
    if pd.api.types.is_float_dtype(expected) and pd.api.types.is_float_dtype(actual):
        e, a = expected.to_numpy(float), actual.to_numpy(float)
        both_nan = np.isnan(e) & np.isnan(a)
        close = np.isclose(e, a, atol=tol.atol, rtol=tol.rtol)
        return both_nan | close
    e_obj = expected.astype(object).to_numpy()
    a_obj = actual.astype(object).to_numpy()
    out = np.empty(len(e_obj), dtype=bool)
    for i, (ev, av) in enumerate(zip(e_obj, a_obj, strict=True)):
        if ev is None or av is None:
            out[i] = ev is None and av is None
            continue
        try:
            out[i] = bool(np.isclose(float(ev), float(av), atol=tol.atol, rtol=tol.rtol))
        except (TypeError, ValueError):
            out[i] = str(ev) == str(av)
    return out


def compare_frames(
    expected: pd.DataFrame, actual: pd.DataFrame, sheet: str, tol: Tolerance, report: DiffReport
) -> None:
    exp, act = _normalise(expected), _normalise(actual)
    missing = [c for c in exp.columns if c not in act.columns]
    extra = [c for c in act.columns if c not in exp.columns]
    if missing:
        report.problems.append(f"sheet '{sheet}': missing columns {missing}")
    if extra:
        report.problems.append(f"sheet '{sheet}': unexpected extra columns {extra}")
    if missing or extra:
        return
    if not tol.ignore_column_order and list(exp.columns) != list(act.columns):
        report.problems.append(
            f"sheet '{sheet}': column order {list(act.columns)} != {list(exp.columns)}"
        )
    act = act[list(exp.columns)]
    if len(exp) != len(act):
        report.problems.append(f"sheet '{sheet}': expected {len(exp)} rows, got {len(act)} rows")
        return
    if tol.ignore_row_order:
        exp, act = _sort(exp), _sort(act)
    for col in exp.columns:
        equal = _values_equal(exp[col], act[col], tol)
        report.cells_compared += len(equal)
        bad = np.flatnonzero(~equal)
        if len(bad):
            samples = "; ".join(
                f"row {i + 1}: expected {_fmt(exp[col].iloc[i])}, got {_fmt(act[col].iloc[i])}"
                for i in bad[:MAX_EXAMPLES]
            )
            report.problems.append(
                f"sheet '{sheet}', column '{col}': {len(bad)} of {len(equal)} values differ "
                f"({samples})"
            )


def compare_files(expected: Path, actual: Path, tol: Tolerance | None = None) -> DiffReport:
    """Compare every sheet of `expected` with the same-named sheet of `actual`."""
    tol = tol or Tolerance()
    report = DiffReport()
    exp_tables, act_tables = read_tables(expected), read_tables(actual)
    if Path(expected).suffix.lower() == ".csv" and len(act_tables) == 1:
        # CSV has no sheet names; compare the single table whatever it is called.
        act_tables = {next(iter(exp_tables)): next(iter(act_tables.values()))}
    for sheet, exp_frame in exp_tables.items():
        if sheet not in act_tables:
            report.problems.append(f"missing sheet '{sheet}' (output has {sorted(act_tables)})")
            continue
        report.sheets_compared += 1
        compare_frames(exp_frame, act_tables[sheet], sheet, tol, report)
    extra = sorted(set(act_tables) - set(exp_tables))
    if extra:
        report.problems.append(f"unexpected extra sheets {extra}")
    return report
