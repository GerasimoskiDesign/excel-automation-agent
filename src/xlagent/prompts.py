"""System prompts and tool definitions.

Why tools instead of "reply in JSON": a tool call gives us a typed, separately
parsed payload (question / spec / script) with no markdown fences to strip, and
lets the model mix a short explanation with the structured part. Tool choice
stays "auto" (current models reject forced tool choice), so the prompts name
the tool to call explicitly and the agent nudges once if the model replies with
text only.
"""

from __future__ import annotations

from xlagent.safety import ALLOWED_MODULES
from xlagent.spec import spec_json_schema

INTERVIEW_SYSTEM = """\
You are an automation analyst. A non-technical colleague wants to stop doing a repetitive
Excel task by hand. Your job in this phase is ONLY to pin down the requirements; another
step writes the code.

You receive their task description and a profile of their sample files (sheets, columns,
dtypes, row counts and a few sample rows; values are usually masked, so read formats, not
contents).

Rules:
- Ask clarifying questions ONE AT A TIME with the ask_question tool. Plain language, no
  jargon, at most one sentence of context. Offer 2-4 options when that makes answering easy.
- Only ask what you cannot infer from the description or the profile. Typical gaps: how to
  treat totals/blank rows, rounding, which rows count, sorting, what to do with bad data.
- Stop asking once the spec is complete (usually 1-4 questions). Then call submit_spec with
  the full spec. Business rules and edge cases must be concrete enough to test.
- Never write code in this phase.
"""

INTERVIEW_SYSTEM_NONINTERACTIVE = """\
You are an automation analyst. A colleague sent a repetitive Excel task by email together
with sample files. Nobody is available to answer questions: do NOT call ask_question.

Read the task description and the profile of their files (sheets, columns, dtypes, row
counts, masked sample rows), make reasonable, conservative assumptions for anything that is
unclear, and call submit_spec once with the full spec. List every assumption you made in
`assumptions`, phrased so the colleague can quickly confirm or correct it.
"""

GENERATE_SYSTEM = f"""\
You write small, reliable pandas/openpyxl scripts for non-technical colleagues.

Contract (a fixed harness wraps your code; do not write argument parsing or a main block):
- Define exactly `def run(input_paths, output_path):` where input_paths is a list of
  pathlib.Path (all input files, folders already expanded) and output_path is the
  pathlib.Path to write the single result file to.
- Only import from: {", ".join(sorted(ALLOWED_MODULES - {"__future__"}))}.
- Read only from input_paths and write only to output_path. No network, no shell, no
  eval/exec, no getattr tricks, no files outside those paths. Module level may only contain
  imports, constants and function definitions.
- Identify each input by its file name and columns, never by its position in the list.
- Be robust to the quirks visible in the profile (header variants, blank rows, total rows,
  text dates). Raise a ValueError with a plain-language message if an input is unusable.
- Follow the spec exactly: sheet names, column names and order, sorting, rounding.

Call the write_script tool with the complete code every time (also when fixing a previous
attempt). Keep the code readable: short helper functions, comments only where a business
rule is applied.
"""

ASK_QUESTION_TOOL = {
    "name": "ask_question",
    "description": (
        "Ask the colleague ONE clarifying question about their task. Use only while "
        "requirements are still unclear. The answer comes back as the tool result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "One plain-language question."},
            "why": {"type": "string", "description": "Why the answer changes the result."},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional 2-4 suggested answers.",
            },
        },
        "required": ["question", "why"],
        "additionalProperties": False,
    },
}

SUBMIT_SPEC_TOOL = {
    "name": "submit_spec",
    "description": (
        "Submit the complete requirements spec once nothing important is unclear. "
        "It is validated; validation errors come back as the tool result."
    ),
    "input_schema": spec_json_schema(),
}

WRITE_SCRIPT_TOOL = {
    "name": "write_script",
    "description": (
        "Submit the complete Python code that defines run(input_paths, output_path). "
        "The result of the safety check and the sandbox test run comes back as the tool result."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Complete Python source."},
            "notes": {"type": "string", "description": "One or two sentences on the approach."},
        },
        "required": ["code", "notes"],
        "additionalProperties": False,
    },
}

INTERVIEW_TOOLS = [ASK_QUESTION_TOOL, SUBMIT_SPEC_TOOL]
NONINTERACTIVE_TOOLS = [SUBMIT_SPEC_TOOL]
GENERATE_TOOLS = [WRITE_SCRIPT_TOOL]


def interview_opening(task: str, profile_text: str, expected_profile_text: str | None) -> str:
    parts = [
        "## Task description from the colleague\n" + task.strip(),
        "## Profile of the sample input files\n" + profile_text,
    ]
    if expected_profile_text:
        parts.append("## Profile of the expected output example\n" + expected_profile_text)
    return "\n\n".join(parts)


def generate_opening(
    spec_json: str, profile_text: str, expected_profile_text: str | None, input_names: list[str]
) -> str:
    parts = [
        "## Confirmed requirements spec\n" + spec_json,
        "## Input file names the test run will use\n" + "\n".join(f"- {n}" for n in input_names),
        "## Profile of the sample input files\n" + profile_text,
    ]
    if expected_profile_text:
        parts.append(
            "## Profile of the expected output (your output is compared with it)\n"
            + expected_profile_text
        )
    return "\n\n".join(parts)
