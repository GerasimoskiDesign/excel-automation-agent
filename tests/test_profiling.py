import json

import numpy as np
import pandas as pd

from xlagent.profiling import (
    MASKED_DATE,
    MAX_SAMPLE_ROWS,
    mask_frame,
    mask_value,
    profile_files,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Customer": ["Alex Brook", "Sam Hill", None],
            "Invoice": ["INV-2026/07", "INV-2026/08", "INV-2026/09"],
            "Qty": [3, -12, 450],
            "Amount": [1018.55, 7.5, np.nan],
            "Date": pd.to_datetime(["2026-07-04", None, "2026-09-30"]),
            "Paid": [True, False, True],
        }
    )


def test_masking_keeps_shape_columns_and_dtypes():
    frame = _frame()
    masked = mask_frame(frame)
    assert masked.shape == frame.shape
    assert list(masked.columns) == list(frame.columns)
    assert dict(masked.dtypes) == dict(frame.dtypes)


def test_masking_hides_every_value():
    frame = _frame()
    masked = mask_frame(frame)
    text = masked.astype(str).to_string()
    for secret in ("Alex", "Brook", "2026", "1018.55", "450", "INV"):
        assert secret not in text
    assert masked.loc[0, "Invoice"] == "XXX-9999/99"  # format survives
    assert masked.loc[1, "Qty"] == -99  # sign and digit count survive
    assert masked.loc[0, "Amount"] == 9999.99
    assert masked.loc[0, "Date"] == MASKED_DATE


def test_masking_preserves_missing_values():
    masked = mask_frame(_frame())
    assert masked["Customer"].isna().tolist() == [False, False, True]
    assert masked["Amount"].isna().tolist() == [False, False, True]
    assert masked["Date"].isna().tolist() == [False, True, False]


def test_mask_value_scalars():
    assert mask_value("abc-12") == "xxx-99"
    assert mask_value(0) == 9
    assert mask_value(None) is None
    assert mask_value(True) is False


def test_profile_sends_at_most_five_masked_rows(sales_inputs):
    profile = profile_files(sales_inputs[:2])
    sheet = profile.sheets[0]
    assert len(sheet.sample_rows) <= MAX_SAMPLE_ROWS
    assert sheet.sample_is_masked
    assert sheet.rows > MAX_SAMPLE_ROWS
    text = profile.to_prompt_text()
    assert "USB-C Cable" not in text and "Power Bank" not in text
    json.loads(text)  # valid JSON for the prompt


def test_profile_toggle_shares_real_values(sales_inputs):
    text = profile_files(sales_inputs[:3], share_real_values=True).to_prompt_text()
    assert any(p in text for p in ("USB-C Cable", "Power Bank", "Phone Case"))


def test_profile_is_deterministic(sales_inputs):
    a = profile_files(list(reversed(sales_inputs))).to_prompt_text()
    b = profile_files(sales_inputs).to_prompt_text()
    assert a == b
