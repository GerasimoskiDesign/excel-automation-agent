"""Static safety check for generated scripts, run BEFORE anything executes.

Why an AST allowlist instead of a regex denylist: regexes are trivially fooled
by aliases (`import subprocess as s`) and string building. Walking the syntax
tree lets us reason about what is imported and what is called, and an import
*allowlist* means anything we did not think of is rejected by default.

This is a guard-rail against a well-meaning model doing something careless
(deleting files, calling the network), not a security boundary against hostile
code - Python is too dynamic for static analysis to be airtight. See the
"Safety model" section of the README.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

ALLOWED_MODULES = frozenset(
    {
        # The allowlist from the requirements...
        "pandas",
        "openpyxl",
        "numpy",
        "datetime",
        "pathlib",
        "re",
        "math",
        "collections",
        # ...plus pure-computation stdlib modules that cannot touch the OS.
        "__future__",
        "typing",
        "calendar",
        "decimal",
        "itertools",
        "functools",
        "statistics",
    }
)

DENIED_BUILTINS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "input",
        "exit",
        "quit",
    }
)

# Attribute names that are dangerous on *any* receiver. Matching by attribute
# name (not by resolved module) also catches reach-through like
# `pd.io.common.os.system(...)`.
DENIED_ATTRIBUTES = frozenset(
    {
        "system",
        "popen",
        "Popen",
        "spawn",
        "fork",
        "kill",
        "rmtree",
        "unlink",
        "rmdir",
        "chmod",
        "chown",
        "symlink_to",
        "hardlink_to",
        "read_pickle",
        "to_pickle",
        "read_sql",
        "read_sql_query",
        "read_sql_table",
        "to_sql",
        "read_html",
        "read_clipboard",
        "to_clipboard",
        "eval",
        "exec",
    }
)

ALLOWED_DUNDERS = frozenset({"__name__", "__init__"})
PATH_TAKING_CALLS = frozenset(
    {
        "open",
        "Path",
        "PurePath",
        "read_csv",
        "read_excel",
        "to_csv",
        "to_excel",
        "ExcelWriter",
        "load_workbook",
        "ExcelFile",
    }
)
URL_LITERAL = re.compile(r"^\s*(https?|ftp|file|s3|gs)://", re.IGNORECASE)
ABSOLUTE_LITERAL = re.compile(r"^(/.|[A-Za-z]:[\\/]|\\\\|~)")
TRAVERSAL_LITERAL = re.compile(r"(^|[\\/])\.\.([\\/]|$)")
REQUIRED_FUNCTION = "run"


@dataclass(frozen=True)
class Violation:
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: [{self.rule}] {self.message}"


@dataclass
class SafetyReport:
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    def summary(self) -> str:
        if self.ok:
            return "passed"
        return "\n".join(str(v) for v in self.violations)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[Violation] = []

    def _add(self, node: ast.AST, rule: str, message: str) -> None:
        self.violations.append(Violation(getattr(node, "lineno", 0), rule, message))

    # -- imports ---------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in ALLOWED_MODULES:
                self._add(node, "import", f"module '{alias.name}' is not on the allowlist")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            self._add(node, "import", "relative imports are not allowed")
        top = (node.module or "").split(".")[0]
        if top not in ALLOWED_MODULES:
            self._add(node, "import", f"module '{node.module}' is not on the allowlist")
        for alias in node.names:
            if alias.name in DENIED_ATTRIBUTES or alias.name in DENIED_BUILTINS:
                self._add(node, "denied-call", f"importing '{alias.name}' is not allowed")
        self.generic_visit(node)

    # -- names and attributes -------------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if node.id in DENIED_BUILTINS:
            self._add(node, "denied-call", f"'{node.id}' is not allowed")
        elif node.id.startswith("__") and node.id.endswith("__") and node.id not in ALLOWED_DUNDERS:
            self._add(node, "dunder", f"access to '{node.id}' is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in DENIED_ATTRIBUTES:
            self._add(node, "denied-call", f"'.{node.attr}' is not allowed")
        elif (
            node.attr.startswith("__")
            and node.attr.endswith("__")
            and node.attr not in ALLOWED_DUNDERS
        ):
            self._add(node, "dunder", f"access to '.{node.attr}' is not allowed")
        self.generic_visit(node)

    # -- calls and literals ---------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        if name in PATH_TAKING_CALLS:
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    text = arg.value
                    if ABSOLUTE_LITERAL.match(text) or TRAVERSAL_LITERAL.search(text):
                        self._add(
                            node,
                            "path",
                            f"{name}() with path {text!r} points outside the work directory",
                        )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and URL_LITERAL.match(node.value):
            self._add(node, "network", f"URL literal {node.value[:40]!r} is not allowed")


def _check_contract(tree: ast.Module) -> list[Violation]:
    """The harness imports the script and calls run(input_paths, output_path).

    Why restrict top-level statements: code at module level would run outside
    the harness's error handling, and side effects at import time make the
    script behave differently when packaged.
    """
    problems: list[Violation] = []
    has_run = False
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == REQUIRED_FUNCTION:
            has_run = True
            if len(stmt.args.args) != 2:
                problems.append(
                    Violation(stmt.lineno, "contract", "run() must take (input_paths, output_path)")
                )
        allowed = (
            ast.Import,
            ast.ImportFrom,
            ast.FunctionDef,
            ast.ClassDef,
            ast.Assign,
            ast.AnnAssign,
        )
        is_docstring = isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
        if not isinstance(stmt, allowed) and not is_docstring:
            problems.append(
                Violation(
                    stmt.lineno,
                    "contract",
                    f"top-level {type(stmt).__name__} statement; put logic inside run()",
                )
            )
    if not has_run:
        problems.append(Violation(0, "contract", "missing def run(input_paths, output_path)"))
    return problems


def check_script(source: str) -> SafetyReport:
    """Check generated code against the allowlist, denylist and harness contract."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return SafetyReport([Violation(exc.lineno or 0, "syntax", exc.msg)])
    checker = _Checker()
    checker.visit(tree)
    violations = checker.violations + _check_contract(tree)
    violations.sort(key=lambda v: (v.line, v.rule))
    return SafetyReport(violations)
