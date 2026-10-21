import pytest

from tests.conftest import SOURCES
from xlagent.safety import check_script

OK_BODY = """
import pandas as pd


def run(input_paths, output_path):
{body}
"""


def wrap(body: str, header: str = "") -> str:
    indented = "\n".join("    " + line for line in body.strip().splitlines())
    return header + OK_BODY.format(body=indented)


@pytest.mark.parametrize(
    "code, rule",
    [
        (wrap("os.system('dir')", "import os\n"), "import"),
        (wrap("subprocess.run(['ls'])", "import subprocess\n"), "import"),
        (wrap("check_output(['ls'])", "from subprocess import check_output\n"), "import"),
        (wrap("eval('1+1')"), "denied-call"),
        (wrap("exec('x = 1')"), "denied-call"),
        (wrap("__import__('os').system('dir')"), "denied-call"),
        (wrap("open('/etc/passwd').read()"), "path"),
        (wrap("open('C:\\\\Windows\\\\win.ini').read()"), "path"),
        (wrap("open('../secrets.txt').read()"), "path"),
        (wrap("pd.read_csv('../../other/data.csv')"), "path"),
        (wrap("socket.create_connection(('example.org', 80))", "import socket\n"), "import"),
        (wrap("requests.get('x')", "import requests\n"), "import"),
        (wrap("pd.read_csv('https://example.org/data.csv')"), "network"),
        (wrap("shutil.rmtree('.')", "import shutil\n"), "import"),
        (wrap("output_path.parent.rmdir()"), "denied-call"),
        (wrap("input_paths[0].unlink()"), "denied-call"),
        (wrap("pd.io.common.os.system('dir')"), "denied-call"),
        (wrap("getattr(pd, 'read_pickle')('x')"), "denied-call"),
        (wrap("pd.read_pickle(input_paths[0])"), "denied-call"),
        (wrap("().__class__.__base__.__subclasses__()"), "dunder"),
        (wrap("x = 1", "from . import helpers\n"), "import"),
        (wrap("x = 1", "import importlib\n"), "import"),
    ],
)
def test_each_denylisted_pattern_is_blocked(code, rule):
    report = check_script(code)
    assert not report.ok
    assert rule in {v.rule for v in report.violations}, report.summary()


def test_normal_pandas_script_is_allowed():
    code = '''
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

RATE = 0.2


def run(input_paths, output_path):
    """Sum amounts per customer."""
    frames = [pd.read_excel(p) for p in input_paths if Path(p).suffix == ".xlsx"]
    df = pd.concat(frames, ignore_index=True)
    df["Customer"] = df["Customer"].str.replace(re.compile(r"\\s+"), " ", regex=True)
    totals = defaultdict(float)
    for row in df.itertuples():
        totals[row.Customer] += float(np.round(row.Amount * (1 + RATE), 2))
    with open(output_path.with_suffix(".txt"), "w") as fh:
        fh.write(str(len(totals)))
    with pd.ExcelWriter(output_path) as writer:
        df.to_excel(writer, sheet_name="Data", index=False)
'''
    report = check_script(code)
    assert report.ok, report.summary()


@pytest.mark.parametrize(
    "example", ["sales_consolidation", "asset_depreciation", "attendance_exceptions"]
)
def test_final_fixture_scripts_pass(example):
    code = (SOURCES / example / "attempt_2.py").read_text(encoding="utf-8")
    assert check_script(code).ok


def test_contract_requires_run_function():
    report = check_script("import pandas as pd\n\ndef main(a, b):\n    return 1\n")
    assert any(v.rule == "contract" and "missing def run" in v.message for v in report.violations)


def test_contract_rejects_top_level_side_effects():
    code = "import pandas as pd\npd.read_csv('a.csv')\n\ndef run(a, b):\n    pass\n"
    report = check_script(code)
    assert any(v.rule == "contract" for v in report.violations)


def test_syntax_error_is_reported_not_raised():
    report = check_script("def run(a, b):\n    return (\n")
    assert [v.rule for v in report.violations] == ["syntax"]
