"""The requirements spec the interview produces and the code generator consumes.

Why a strict Pydantic model: the spec is the contract between three parties -
the colleague (who confirms it), the model (which writes code against it) and
the packager (which writes the colleague-facing README from it). Validating it
up front turns vague requirements into an explicit error the model must fix,
instead of a script that silently does something different.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EXCEL_SHEET_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InputFile(_Strict):
    """One kind of input file the script must accept."""

    name_pattern: str = Field(min_length=1, description="e.g. 'sales_<shop>_<YYYY-MM>.xlsx'")
    description: str = Field(min_length=1)
    sheet: str | None = Field(default=None, description="Sheet to read; None for CSV/first sheet")
    key_columns: list[str] = Field(default_factory=list)


class OutputSheet(_Strict):
    name: str = Field(min_length=1, max_length=31)
    columns: list[str] = Field(min_length=1)
    sort_by: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _excel_safe_name(cls, value: str) -> str:
        # Excel refuses to open a workbook with these characters in a sheet name.
        if EXCEL_SHEET_FORBIDDEN.search(value):
            raise ValueError(f"sheet name {value!r} contains a character Excel does not allow")
        return value

    @model_validator(mode="after")
    def _columns_consistent(self) -> OutputSheet:
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(f"sheet {self.name!r} has duplicate column names")
        unknown = [c for c in self.sort_by if c not in self.columns]
        if unknown:
            raise ValueError(f"sheet {self.name!r} sorts by unknown columns {unknown}")
        return self


class OutputFormat(_Strict):
    file_type: Literal["xlsx", "csv"]
    file_name: str = Field(min_length=1)
    sheets: list[OutputSheet] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> OutputFormat:
        if not self.file_name.lower().endswith("." + self.file_type):
            raise ValueError(f"file_name {self.file_name!r} must end with .{self.file_type}")
        if self.file_type == "csv" and len(self.sheets) != 1:
            raise ValueError("a CSV output can only have one sheet")
        names = [s.name.lower() for s in self.sheets]
        if len(set(names)) != len(names):
            raise ValueError("sheet names must be unique (Excel ignores case)")
        return self


class Schedule(_Strict):
    frequency: Literal["ad-hoc", "daily", "weekly", "monthly", "quarterly", "yearly"]
    notes: str = ""


class RequirementsSpec(_Strict):
    """Everything the generator needs to write the script, in plain language."""

    title: str = Field(min_length=3, max_length=80)
    summary: str = Field(min_length=10)
    inputs: list[InputFile] = Field(min_length=1)
    steps: list[str] = Field(min_length=1)
    output_format: OutputFormat
    business_rules: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    schedule: Schedule

    @field_validator("steps", "business_rules", "edge_cases")
    @classmethod
    def _no_blank_items(cls, items: list[str]) -> list[str]:
        if any(not item.strip() for item in items):
            raise ValueError("list items must not be blank")
        return items


class SpecSubmission(_Strict):
    """What the model submits: the spec plus any assumptions it made on its own.

    Assumptions matter most in non-interactive mode (API / n8n), where nobody can
    answer questions: they are echoed back to the requester so a wrong guess is
    visible instead of buried in code.
    """

    spec: RequirementsSpec
    assumptions: list[str] = Field(default_factory=list)


def spec_json_schema() -> dict:
    """JSON schema for the submit_spec tool, generated from the Pydantic model."""
    return SpecSubmission.model_json_schema()
