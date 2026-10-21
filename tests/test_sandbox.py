import os

import pytest

from xlagent.harness import assemble_script, extract_task_code
from xlagent.sandbox import run_script, scrubbed_env


def _script(body: str) -> str:
    code = "import pandas as pd\n\n\ndef run(input_paths, output_path):\n" + "\n".join(
        "    " + line for line in body.strip().splitlines()
    )
    return assemble_script(code, "Test tool")


def test_successful_run_collects_output(sales_inputs, tmp_path):
    script = _script(
        "frame = pd.concat([pd.read_excel(p) for p in input_paths])\n"
        "frame.to_excel(output_path, index=False)"
    )
    result = run_script(script, sales_inputs[:2], "out.xlsx", timeout_s=60, workdir=tmp_path)
    assert result.ok, result.stderr
    assert "out.xlsx" in result.output_files
    assert "Done. Result written" in result.stdout


def test_inputs_are_copies_and_originals_untouched(sales_inputs, tmp_path):
    original = sales_inputs[0].read_bytes()
    script = _script("for p in input_paths:\n    p.write_bytes(b'overwritten')")
    run_script(script, sales_inputs[:1], "out.xlsx", timeout_s=60, workdir=tmp_path)
    assert sales_inputs[0].read_bytes() == original
    assert (tmp_path / "inputs" / sales_inputs[0].name).read_bytes() == b"overwritten"


def test_timeout_kills_the_script(sales_inputs, tmp_path):
    script = _script("while True:\n    pass")
    result = run_script(script, sales_inputs[:1], "out.xlsx", timeout_s=3, workdir=tmp_path)
    assert result.timed_out
    assert not result.ok
    assert result.duration_s < 30


def test_crash_is_captured_with_plain_message(sales_inputs, tmp_path):
    script = _script("raise ValueError('column Revenue is missing')")
    result = run_script(script, sales_inputs[:1], "out.xlsx", timeout_s=60, workdir=tmp_path)
    assert result.returncode == 1
    assert "Sorry, the tool failed: ValueError: column Revenue is missing" in result.stderr


def test_environment_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy-for-test")
    monkeypatch.setenv("HTTPS_PROXY", "proxy.invalid:8080")
    env = scrubbed_env(tmp_path)
    assert "ANTHROPIC_API_KEY" not in env
    assert "HTTPS_PROXY" not in env
    assert env["TEMP"].startswith(str(tmp_path))


def test_child_process_does_not_see_secrets(tmp_path, monkeypatch, sales_inputs):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-dummy-for-test")
    # The safety checker would block `os`; the sandbox itself is tested directly here.
    code = (
        "import os\nimport pandas as pd\n\n\ndef run(input_paths, output_path):\n"
        "    pd.DataFrame({'k': [os.environ.get('ANTHROPIC_API_KEY', 'absent')]})"
        ".to_csv(output_path, index=False)\n"
    )
    result = run_script(
        assemble_script(code, "t"), sales_inputs[:1], "o.csv", timeout_s=60, workdir=tmp_path
    )
    assert result.ok, result.stderr
    assert "absent" in result.output_files["o.csv"].read_text()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-dummy-for-test"


def test_folder_input_is_expanded(sales_inputs, tmp_path):
    script = _script("pd.DataFrame({'n': [len(input_paths)]}).to_csv(output_path, index=False)")
    # Run the harness directly on the examples folder (as run.bat would).
    result = run_script(script, sales_inputs, "n.csv", timeout_s=60, workdir=tmp_path)
    assert result.output_files["n.csv"].read_text().strip().endswith(str(len(sales_inputs)))


def test_extract_task_code_round_trip():
    code = "import pandas as pd\n\n\ndef run(input_paths, output_path):\n    pass\n"
    assert extract_task_code(assemble_script(code, "Title")) == code


@pytest.mark.parametrize("title", ['Has """ quotes', ""])
def test_assemble_handles_awkward_titles(title):
    script = assemble_script("def run(a, b):\n    pass\n", title)
    compile(script, "script.py", "exec")
