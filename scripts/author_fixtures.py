"""Build the HAND-AUTHORED replay cassettes in src/xlagent/fixtures/cassettes/.

No API key was available while building this repo, so the replayed model
responses are written by hand in the exact shape of Anthropic Messages API
responses (thinking + text + tool_use blocks). The scripts the "model" writes
live as readable files in scripts/fixture_sources/<example>/attempt_N.py.

Each scenario is designed to exercise one path of the repair loop:
  sales_consolidation    attempt 1 imports `os` -> rejected by the safety check
  asset_depreciation     attempt 1 starts depreciation a month early -> output mismatch
  attendance_exceptions  attempt 1 crashes on the "-" placeholder for missing punches
  sales (interactive)    two interview questions, then a script that passes first time

To replace them with real recordings, run with XLAGENT_REPLAY=0 XLAGENT_RECORD=1 and an
API key (see README, "Recording real fixtures").

Usage:  python scripts/author_fixtures.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from xlagent.examples_gen import ATTENDANCE_TASK, DEPRECIATION_TASK, SALES_TASK
from xlagent.llm.replay import CASSETTE_FORMAT, session_key
from xlagent.spec import SpecSubmission

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "scripts" / "fixture_sources"
EXAMPLES = ROOT / "examples"
OUT = ROOT / "src" / "xlagent" / "fixtures" / "cassettes"
MODEL = "claude-opus-5-5"

log = logging.getLogger("author_fixtures")

# ---------------------------------------------------------------------------
# Specs the "model" submits
# ---------------------------------------------------------------------------

SALES_SPEC = {
    "title": "Monthly sales summary per shop",
    "summary": (
        "Combine the monthly sales exports of all shops into one workbook with invoices, "
        "units and revenue per shop and month, plus totals per shop and a grand total."
    ),
    "inputs": [
        {
            "name_pattern": "sales_<shop>_<YYYY-MM>.xlsx",
            "description": "One sales export per shop and month; shop name in the file name.",
            "sheet": "Sales",
            "key_columns": ["Date", "Invoice No", "Product", "Qty", "Unit Price", "Line Total"],
        }
    ],
    "steps": [
        "Read every sales file and take the shop name from the file name.",
        "Map the Station headers (Sale Date, Invoice, Item, Quantity, Price, Amount) "
        "to the standard names.",
        "Drop empty rows and the TOTAL row.",
        "Parse text dates in day.month.year format.",
        "Derive Month (YYYY-MM) from the sale date.",
        "Aggregate invoices, units and revenue per shop and month, and per shop.",
        "Add an 'All shops' grand-total row to the shop totals.",
    ],
    "output_format": {
        "file_type": "xlsx",
        "file_name": "sales_summary.xlsx",
        "sheets": [
            {
                "name": "Summary",
                "columns": ["Shop", "Month", "Invoices", "Units", "Revenue"],
                "sort_by": ["Shop", "Month"],
            },
            {
                "name": "Shop Totals",
                "columns": ["Shop", "Invoices", "Units", "Revenue"],
                "sort_by": [],
            },
        ],
    },
    "business_rules": [
        "Invoices = number of distinct invoice numbers, excluding refunds.",
        "Refunds are invoice numbers ending in -R<number>; they have negative quantities and "
        "reduce Units and Revenue.",
        "Revenue = sum of Line Total, rounded to 2 decimals.",
        "Month comes from the sale date, not from the file name.",
        "Shop names are title case (North, Harbour, Station).",
    ],
    "edge_cases": [
        "Harbour files end with a TOTAL row that must be ignored.",
        "North files contain completely empty rows.",
        "Station files use different column names and text dates.",
        "A shop with only refunds in a month gets 0 invoices.",
    ],
    "schedule": {"frequency": "monthly", "notes": "After all shop files for the month arrive."},
}

DEPRECIATION_SPEC = {
    "title": "Fixed asset depreciation schedule 2026",
    "summary": (
        "Build the monthly straight-line depreciation schedule for 2026 from the asset "
        "register, with a yearly total per asset category."
    ),
    "inputs": [
        {
            "name_pattern": "asset_register.xlsx",
            "description": "Fixed asset register, one row per asset.",
            "sheet": "Register",
            "key_columns": [
                "Asset ID",
                "Category",
                "Acquisition Date",
                "Cost",
                "Salvage Value",
                "Useful Life (months)",
                "Disposal Date",
            ],
        }
    ],
    "steps": [
        "Read the asset register.",
        "For each asset compute the monthly charge (Cost - Salvage Value) / Useful Life, "
        "rounded to cents.",
        "For each month of 2026 in which the asset is on the books, compute opening NBV, "
        "depreciation and closing NBV.",
        "Total the 2026 depreciation per category.",
    ],
    "output_format": {
        "file_type": "xlsx",
        "file_name": "depreciation_2026.xlsx",
        "sheets": [
            {
                "name": "Schedule",
                "columns": ["Asset ID", "Month", "Opening NBV", "Depreciation", "Closing NBV"],
                "sort_by": ["Asset ID", "Month"],
            },
            {
                "name": "Category Totals",
                "columns": ["Category", "Assets", "Depreciation 2026"],
                "sort_by": ["Category"],
            },
        ],
    },
    "business_rules": [
        "Depreciation starts in the month after the acquisition month.",
        "The acquisition month appears with zero depreciation.",
        "The final month takes the rounding difference; NBV never goes below salvage value.",
        "Disposed assets are depreciated up to and including the disposal month and do not "
        "appear afterwards.",
        "All amounts are rounded to 2 decimals.",
    ],
    "edge_cases": [
        "Assets fully depreciated before 2026 appear with zero depreciation.",
        "Assets acquired during 2026 appear from their acquisition month.",
        "Assets acquired after 2026 do not appear.",
    ],
    "schedule": {"frequency": "monthly", "notes": "Re-run after the register is updated."},
}

ATTENDANCE_SPEC = {
    "title": "Monthly attendance exceptions",
    "summary": (
        "List missing clock-ins, employees late three or more times, and rows where the "
        "clock-out is before the clock-in, from the monthly time-clock export."
    ),
    "inputs": [
        {
            "name_pattern": "clock_events_<YYYY-MM>.csv",
            "description": "Time-clock export: one row per employee and day; '-' = missing punch.",
            "sheet": None,
            "key_columns": ["Employee ID", "Date", "Clock In", "Clock Out"],
        },
        {
            "name_pattern": "roster.xlsx",
            "description": "Employees with name, team and shift start time.",
            "sheet": "Roster",
            "key_columns": ["Employee ID", "Name", "Shift Start"],
        },
    ],
    "steps": [
        "Read the clock events and the roster and join them on Employee ID.",
        "Parse clock-in, clock-out and shift start times (HH:MM).",
        "Find rows with a clock-out but no clock-in.",
        "Find rows where the clock-out is earlier than the clock-in.",
        "Count late arrivals per employee, excluding those error rows.",
        "Write the three exception lists.",
    ],
    "output_format": {
        "file_type": "xlsx",
        "file_name": "attendance_exceptions.xlsx",
        "sheets": [
            {
                "name": "Missing Clock-In",
                "columns": ["Employee ID", "Name", "Date"],
                "sort_by": ["Employee ID", "Date"],
            },
            {
                "name": "Repeated Late",
                "columns": ["Employee ID", "Name", "Late Days", "Late Dates"],
                "sort_by": ["Employee ID"],
            },
            {
                "name": "Out Before In",
                "columns": ["Employee ID", "Name", "Date", "Clock In", "Clock Out"],
                "sort_by": ["Employee ID", "Date"],
            },
        ],
    },
    "business_rules": [
        "Late = clock-in more than 5 minutes after the shift start.",
        "Repeated Late lists employees with 3 or more late days in the month.",
        "Late Dates is a comma-separated list of ISO dates in date order.",
        "Rows with clock-out before clock-in are data errors and are not counted as late.",
    ],
    "edge_cases": [
        "'-' in Clock In or Clock Out means the punch is missing.",
        "Days without any row are absences and are ignored.",
        "A missing clock-in is not counted as late.",
    ],
    "schedule": {"frequency": "monthly", "notes": "First working day of the next month."},
}

ASSUMPTIONS = {
    "sales_consolidation": [
        "Month is taken from the sale date, not from the file name.",
        "Revenue uses the Line Total column (Qty x Unit Price), rounded to cents.",
        "A refund is an invoice number ending in -R plus digits.",
    ],
    "asset_depreciation": [
        "The schedule covers calendar year 2026 as stated in the task.",
        "Monthly charges are rounded to cents before accumulating.",
        "Assets with a blank Disposal Date are still in use.",
    ],
    "attendance_exceptions": [
        "Shift Start in the roster applies to every working day of the month.",
        "Exactly 5 minutes after shift start is still on time.",
        "A row with a missing clock-out but a valid clock-in is not an exception.",
    ],
}

# ---------------------------------------------------------------------------
# Message builders (Anthropic Messages API response shape)
# ---------------------------------------------------------------------------


def _message(msg_id: str, text: str, tool: dict | None, in_tok: int, out_tok: int) -> dict:
    content: list[dict] = [
        # Opus 5.5 always thinks; by default the thinking text is omitted (empty string).
        {"type": "thinking", "thinking": "", "signature": "hand-authored-fixture"},
        {"type": "text", "text": text},
    ]
    if tool is not None:
        content.append({"type": "tool_use", **tool})
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": "tool_use" if tool is not None else "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def _code(example: str, attempt: int) -> str:
    return (SOURCES / example / f"attempt_{attempt}.py").read_text(encoding="utf-8")


def _tokens(text: str) -> int:
    """Rough token estimate (~3.5 characters per token) - these are hand-authored."""
    return max(1, round(len(text) / 3.5))


def _input_names(example: str) -> list[str]:
    return sorted(p.name for p in (EXAMPLES / example / "inputs").iterdir())


def _spec_message(prefix: str, n: int, spec: dict, assumptions: list[str], text: str) -> dict:
    SpecSubmission.model_validate({"spec": spec, "assumptions": assumptions})  # fail early
    payload = {"spec": spec, "assumptions": assumptions}
    return _message(
        f"msg_replay_{prefix}_{n:02d}",
        text,
        {"id": f"toolu_replay_{prefix}_{n:02d}", "name": "submit_spec", "input": payload},
        in_tok=4200,
        out_tok=_tokens(json.dumps(payload)) + 60,
    )


def _script_message(prefix: str, n: int, code: str, notes: str, text: str, in_tok: int) -> dict:
    return _message(
        f"msg_replay_{prefix}_{n:02d}",
        text,
        {
            "id": f"toolu_replay_{prefix}_{n:02d}",
            "name": "write_script",
            "input": {"code": code, "notes": notes},
        },
        in_tok=in_tok,
        out_tok=_tokens(code) + 80,
    )


def _cassette(label: str, mode: str, task: str, example: str, interactions: list) -> dict:
    return {
        "format": CASSETTE_FORMAT,
        "session_key": session_key(mode, task, _input_names(example)),
        "label": label,
        "source": "hand-authored",
        "model": MODEL,
        "example": example,
        "mode": mode,
        "interactions": interactions,
    }


def sales_noninteractive() -> dict:
    p = "sales"
    return _cassette(
        "sales_consolidation (non-interactive)",
        "noninteractive",
        SALES_TASK,
        "sales_consolidation",
        [
            {
                "stage": "interview",
                "request": {"last_user_message": "<task description + file profile>"},
                "response": _spec_message(
                    p,
                    1,
                    SALES_SPEC,
                    ASSUMPTIONS["sales_consolidation"],
                    "The description and the profiles cover almost everything. I made a few "
                    "assumptions and listed them so they can be checked.",
                ),
            },
            {
                "stage": "generate",
                "request": {"last_user_message": "<confirmed spec + profiles>"},
                "response": _script_message(
                    p,
                    2,
                    _code("sales_consolidation", 1),
                    "Normalise headers per shop, drop blank and TOTAL rows, then aggregate.",
                    "Here is the script. It maps each shop's headers to one vocabulary before "
                    "aggregating.",
                    5200,
                ),
            },
            {
                "stage": "repair",
                "request": {"last_user_message": "<safety check: module 'os' not allowed>"},
                "response": _script_message(
                    p,
                    3,
                    _code("sales_consolidation", 2),
                    "Same logic; file names now come from pathlib's Path.stem instead of os.path.",
                    "Understood - `os` is not on the allowlist. The paths are already pathlib "
                    "objects, so I use `path.stem` instead.",
                    6900,
                ),
            },
        ],
    )


def sales_interactive() -> dict:
    p = "sales_chat"
    q1 = {
        "question": "When a sale's date is in a different month than the file name (for "
        "example a late entry), which month should it count in?",
        "why": "It decides which row of the Summary a late entry lands in.",
        "options": ["The month of the sale date", "The month in the file name"],
    }
    q2 = {
        "question": "Should refunds also be listed separately, or only netted off against "
        "units and revenue as in your example?",
        "why": "A separate refund column would change the layout of both sheets.",
        "options": ["Only net them off", "Add a Refunds column"],
    }
    return _cassette(
        "sales_consolidation (interactive interview)",
        "interactive",
        SALES_TASK,
        "sales_consolidation",
        [
            {
                "stage": "interview",
                "request": {"last_user_message": "<task description + file profile>"},
                "response": _message(
                    f"msg_replay_{p}_01",
                    "Thanks, the description is clear. Two quick questions before I write "
                    "the spec.",
                    {"id": f"toolu_replay_{p}_01", "name": "ask_question", "input": q1},
                    3900,
                    95,
                ),
            },
            {
                "stage": "interview",
                "request": {"last_user_message": "The month of the sale date"},
                "response": _message(
                    f"msg_replay_{p}_02",
                    "Got it.",
                    {"id": f"toolu_replay_{p}_02", "name": "ask_question", "input": q2},
                    4050,
                    90,
                ),
            },
            {
                "stage": "interview",
                "request": {"last_user_message": "Only net them off"},
                "response": _spec_message(
                    p,
                    3,
                    SALES_SPEC,
                    [],
                    "That's everything I need. Here is the spec - please check it before I "
                    "write the script.",
                ),
            },
            {
                "stage": "generate",
                "request": {"last_user_message": "<confirmed spec + profiles>"},
                "response": _script_message(
                    p,
                    4,
                    _code("sales_consolidation", 2),
                    "Normalise headers per shop, drop blank and TOTAL rows, then aggregate.",
                    "Here is the script.",
                    5200,
                ),
            },
        ],
    )


def depreciation_noninteractive() -> dict:
    p = "depr"
    return _cassette(
        "asset_depreciation (non-interactive)",
        "noninteractive",
        DEPRECIATION_TASK,
        "asset_depreciation",
        [
            {
                "stage": "interview",
                "request": {"last_user_message": "<task description + file profile>"},
                "response": _spec_message(
                    p,
                    1,
                    DEPRECIATION_SPEC,
                    ASSUMPTIONS["asset_depreciation"],
                    "The policy is fully described. Spec below with my assumptions listed.",
                ),
            },
            {
                "stage": "generate",
                "request": {"last_user_message": "<confirmed spec + profiles>"},
                "response": _script_message(
                    p,
                    2,
                    _code("asset_depreciation", 1),
                    "Accumulated depreciation is computed from the month index, so the opening "
                    "NBV for 2026 does not need a loop over previous years.",
                    "Here is the depreciation script.",
                    4800,
                ),
            },
            {
                "stage": "repair",
                "request": {"last_user_message": "<diff: Opening NBV 282 of 328 values differ>"},
                "response": _script_message(
                    p,
                    3,
                    _code("asset_depreciation", 2),
                    "Depreciation now starts in the month after acquisition.",
                    "The diff shows every value one month ahead of the expected schedule: I "
                    "started depreciating in the acquisition month, but the policy says the "
                    "month after. Fixed.",
                    6400,
                ),
            },
        ],
    )


def attendance_noninteractive() -> dict:
    p = "attn"
    return _cassette(
        "attendance_exceptions (non-interactive)",
        "noninteractive",
        ATTENDANCE_TASK,
        "attendance_exceptions",
        [
            {
                "stage": "interview",
                "request": {"last_user_message": "<task description + file profile>"},
                "response": _spec_message(
                    p,
                    1,
                    ATTENDANCE_SPEC,
                    ASSUMPTIONS["attendance_exceptions"],
                    "Clear task. Spec below; please check the three assumptions.",
                ),
            },
            {
                "stage": "generate",
                "request": {"last_user_message": "<confirmed spec + profiles>"},
                "response": _script_message(
                    p,
                    2,
                    _code("attendance_exceptions", 1),
                    "Times are converted to minutes after midnight for all comparisons.",
                    "Here is the attendance script.",
                    4600,
                ),
            },
            {
                "stage": "repair",
                "request": {"last_user_message": "<stderr: time data '-' doesn't match format>"},
                "response": _script_message(
                    p,
                    3,
                    _code("attendance_exceptions", 2),
                    "Unparseable times (the '-' placeholder) are now treated as missing.",
                    "The crash comes from the '-' placeholder for missing punches. I now parse "
                    "with errors='coerce' so those become missing values, which is exactly what "
                    "the missing clock-in rule needs.",
                    6100,
                ),
            },
        ],
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    OUT.mkdir(parents=True, exist_ok=True)
    builders = {
        "sales_consolidation.json": sales_noninteractive,
        "sales_consolidation_interactive.json": sales_interactive,
        "asset_depreciation.json": depreciation_noninteractive,
        "attendance_exceptions.json": attendance_noninteractive,
    }
    for name, build in builders.items():
        path = OUT / name
        path.write_text(
            json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        log.info("wrote %s", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
