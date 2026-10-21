"""Build the deliverable zip the colleague double-clicks.

Contents: script.py (harness + generated logic), README_for_colleague.md,
run.bat and requirements.txt. The README is rendered from the confirmed spec
with a fixed template rather than written by the model: it must describe what
the code was *tested* to do, and a template cannot drift from the spec.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from xlagent.spec import SpecSubmission
from xlagent.xlsx_io import FIXED_ZIP_DATE

REQUIREMENTS = "pandas>=2.2\nopenpyxl>=3.1\nnumpy>=1.26\n"

RUN_BAT = r"""@echo off
setlocal
cd /d "%~dp0"
echo.
echo  {title}
echo  -------------------------------------------------------------
echo  Drag the input file (or the folder with all input files) into
echo  this window and press Enter.
echo.
set "INPUT="
set /p "INPUT=Input: "
if not defined INPUT (
  echo No input given.
  pause
  exit /b 1
)
rem Dragging a path into the window adds quotes; remove them.
set "INPUT=%INPUT:"=%"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up Python packages, this takes a minute...
  python -m venv .venv || goto :nopython
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :failed
)
if not exist "output" mkdir "output"
".venv\Scripts\python.exe" script.py --output "output\{output_name}" "%INPUT%" || goto :failed
echo.
echo Finished. The result is in the "output" folder next to this file.
start "" "output"
pause
exit /b 0

:nopython
echo Python was not found. Please ask IT to install Python 3.11 or newer.
pause
exit /b 1

:failed
echo.
echo Something went wrong. Please send the message above to the person who gave you this tool.
pause
exit /b 1
"""


def _bat_safe(text: str) -> str:
    """Strip characters cmd.exe would interpret inside an echo line."""
    return re.sub(r"[&|<>^%!\"]", "", text)


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def colleague_readme(submission: SpecSubmission, tested_on: list[str], attempts: int) -> str:
    spec = submission.spec
    out = spec.output_format
    sheets = "\n".join(
        f"- **{s.name}**: {', '.join(s.columns)}"
        + (f" (sorted by {', '.join(s.sort_by)})" if s.sort_by else "")
        for s in out.sheets
    )
    inputs = "\n".join(f"- `{i.name_pattern}`: {i.description}" for i in spec.inputs)
    steps = "\n".join(f"{n}. {s}" for n, s in enumerate(spec.steps, 1))
    schedule = spec.schedule.frequency + (
        f" ({spec.schedule.notes})" if spec.schedule.notes else ""
    )
    return f"""# {spec.title}

{spec.summary}

## How to use it (Windows)

1. Unzip this folder somewhere, e.g. your Desktop.
2. Double-click **run.bat**.
3. Drag your input file, or the folder that contains all input files, into the window and
   press Enter.
4. The result `{out.file_name}` appears in the `output` folder next to run.bat.

The first run takes a minute because it installs the Python packages it needs (Python 3.11
or newer must be installed).

## What it expects

{inputs}

## What it produces

`{out.file_name}` with these sheets:

{sheets}

## What it does, step by step

{steps}

## Rules it applies

{_bullets(spec.business_rules, "No special business rules.")}

## Special cases it handles

{_bullets(spec.edge_cases, "None listed.")}

## Assumptions (please check these)

{_bullets(submission.assumptions, "None - everything was confirmed in the interview.")}

## How it was tested

The script was run on these sample files before delivery: {", ".join(tested_on)}.
It needed {attempts} attempt(s) to pass the automatic checks. Suggested schedule: {schedule}.

If the result looks wrong, or your files change format, send the input file and the error
message to the person who gave you this tool.
"""


def build_zip(
    script: str,
    submission: SpecSubmission,
    tested_on: list[str],
    attempts: int,
    dest: Path | None = None,
) -> bytes:
    """Return the zip bytes (and write them to `dest` if given). Deterministic."""
    out = submission.spec.output_format
    files = {
        "script.py": script,
        "README_for_colleague.md": colleague_readme(submission, tested_on, attempts),
        "run.bat": RUN_BAT.format(title=_bat_safe(submission.spec.title), output_name=out.file_name)
        .replace("\r\n", "\n")
        .replace("\n", "\r\n"),  # cmd.exe needs CRLF line endings
        "requirements.txt": REQUIREMENTS,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3  # same bytes on Windows and Linux
            zf.writestr(info, files[name].encode("utf-8"))
    data = buffer.getvalue()
    if dest is not None:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
    return data
