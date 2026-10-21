"""Small helpers for reading and writing spreadsheet files.

Why a helper module: openpyxl stamps the current time into every .xlsx it saves
(zip entry dates and docProps/core.xml). That makes "re-running produces
identical outputs" impossible unless we normalise those timestamps, so all
files this repo generates go through :func:`write_xlsx`.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pandas as pd

FIXED_ZIP_DATE = (2026, 1, 1, 0, 0, 0)
FIXED_ISO = "2026-01-01T00:00:00Z"
TABLE_SUFFIXES = (".xlsx", ".xlsm", ".csv")

_CORE_TS = re.compile(
    rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:(?:created|modified)>)"
)


def normalize_xlsx(path: Path) -> None:
    """Rewrite an .xlsx in place with fixed timestamps so the bytes are reproducible."""
    src = Path(path).read_bytes()
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(src)) as zin,
        zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in sorted(zin.infolist(), key=lambda i: i.filename):
            data = zin.read(info.filename)
            if info.filename == "docProps/core.xml":
                data = _CORE_TS.sub(rb"\g<1>" + FIXED_ISO.encode() + rb"\g<2>", data)
            new_info = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_DATE)
            new_info.compress_type = zipfile.ZIP_DEFLATED
            new_info.external_attr = 0o644 << 16
            zout.writestr(new_info, data)
    Path(path).write_bytes(out.getvalue())


def write_xlsx(path: Path, sheets: dict[str, pd.DataFrame]) -> Path:
    """Write several DataFrames to one workbook (one sheet each), deterministically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
    normalize_xlsx(path)
    return path


def write_csv(path: Path, frame: pd.DataFrame) -> Path:
    """Write a CSV with Unix line endings so the bytes match across operating systems."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    return path


def read_tables(path: Path) -> dict[str, pd.DataFrame]:
    """Read every sheet of a workbook (or a CSV as one pseudo-sheet named after the file)."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return {path.stem: pd.read_csv(path, keep_default_na=True)}
    return pd.read_excel(path, sheet_name=None, engine="openpyxl")


def is_table_file(path: Path) -> bool:
    """True for file types the agent knows how to profile and pass to a script."""
    return Path(path).suffix.lower() in TABLE_SUFFIXES
