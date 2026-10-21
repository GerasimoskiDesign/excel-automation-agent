"""Local profiling of the uploaded files, and masking of sample values.

Why profile locally: the model needs the *shape* of the data (sheets, columns,
types, a few example rows) to write correct code, but the colleague's files
may contain personal or commercial data. By default only the schema and up to
five masked rows leave the machine; real values are sent only when the user
explicitly flips the toggle.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from xlagent.xlsx_io import read_tables

MAX_SAMPLE_ROWS = 5
MASKED_DATE = pd.Timestamp("2000-01-01")


def mask_value(value: Any) -> Any:
    """Hide a value while keeping its type and rough shape.

    - strings: letters become x/X, digits become 9, punctuation and length stay
      (so "INV-2026/07" -> "XXX-9999/99": the model still sees the format)
    - numbers: every digit becomes 9, sign and decimal places stay
    - dates/times: replaced with a fixed date, time-of-day kept as 00:00
    - booleans become False; missing values stay missing (missingness is useful
      for the model and is not sensitive)
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return value
    if value is pd.NaT or value is pd.NA:
        return value
    if isinstance(value, bool | np.bool_):
        return False
    if isinstance(value, int | np.integer):
        digits = len(str(abs(int(value))))
        masked = int("9" * digits)
        return -masked if value < 0 else masked
    if isinstance(value, float | np.floating):
        text = repr(float(value))
        if "e" in text or "inf" in text:
            return 9.0
        return float(re.sub(r"\d", "9", text))
    if isinstance(value, pd.Timestamp | np.datetime64):
        return MASKED_DATE
    if isinstance(value, str):
        out = re.sub(r"[A-Z]", "X", value)
        out = re.sub(r"[a-z]", "x", out)
        out = re.sub(r"[^\x00-\x7f]", "x", out)
        return re.sub(r"\d", "9", out)
    return "<masked>"


def mask_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Mask every cell; the result has the same shape, columns and dtypes."""
    masked = frame.copy()
    for col in masked.columns:
        series = masked[col]
        if pd.api.types.is_datetime64_any_dtype(series):
            masked[col] = series.where(series.isna(), MASKED_DATE)
        else:
            masked[col] = series.map(mask_value).astype(series.dtype, errors="ignore")
    return masked


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    non_null: int
    unique: int


@dataclass
class SheetProfile:
    file: str
    sheet: str
    rows: int
    columns: list[ColumnProfile]
    sample_rows: list[dict[str, Any]]
    sample_is_masked: bool


@dataclass
class DatasetProfile:
    sheets: list[SheetProfile] = field(default_factory=list)

    def to_prompt_text(self) -> str:
        """Compact JSON for the model prompt (stable key order for reproducible prompts)."""
        return json.dumps(asdict(self), indent=1, sort_keys=True, default=str)


def _jsonable(value: Any) -> Any:
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    return value


def profile_sheet(
    file_name: str, sheet: str, frame: pd.DataFrame, share_real_values: bool = False
) -> SheetProfile:
    columns = [
        ColumnProfile(
            name=str(col),
            dtype=str(frame[col].dtype),
            non_null=int(frame[col].notna().sum()),
            unique=int(frame[col].nunique(dropna=True)),
        )
        for col in frame.columns
    ]
    sample = frame.head(MAX_SAMPLE_ROWS)
    if not share_real_values:
        sample = mask_frame(sample)
    rows = [
        {str(k): _jsonable(v) for k, v in record.items()}
        for record in sample.to_dict(orient="records")
    ]
    return SheetProfile(
        file=file_name,
        sheet=sheet,
        rows=len(frame),
        columns=columns,
        sample_rows=rows,
        sample_is_masked=not share_real_values,
    )


def profile_files(paths: list[Path], share_real_values: bool = False) -> DatasetProfile:
    """Profile every sheet of every file. Files are sorted so the prompt is stable."""
    profile = DatasetProfile()
    for path in sorted(paths, key=lambda p: Path(p).name):
        for sheet, frame in read_tables(Path(path)).items():
            profile.sheets.append(profile_sheet(Path(path).name, sheet, frame, share_real_values))
    return profile
