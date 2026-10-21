import io
import zipfile

from xlagent.harness import assemble_script
from xlagent.packaging import build_zip


def _zip(submission, code="def run(a, b):\n    pass\n"):
    return build_zip(assemble_script(code, submission.spec.title), submission, ["in.xlsx"], 2)


def test_zip_contents(sales_submission):
    with zipfile.ZipFile(io.BytesIO(_zip(sales_submission))) as zf:
        names = sorted(zf.namelist())
        assert names == ["README_for_colleague.md", "requirements.txt", "run.bat", "script.py"]
        bat = zf.read("run.bat")
        readme = zf.read("README_for_colleague.md").decode()
        reqs = zf.read("requirements.txt").decode()
        script = zf.read("script.py").decode()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")  # CRLF only
    assert b"set /p" in bat and b"sales_summary.xlsx" in bat
    assert "Double-click **run.bat**" in readme
    assert "Invoices = number of distinct invoice numbers" in readme
    assert "It needed 2 attempt(s)" in readme
    assert "pandas" in reqs and "openpyxl" in reqs
    compile(script, "script.py", "exec")


def test_zip_is_deterministic(sales_submission):
    assert _zip(sales_submission) == _zip(sales_submission)


def test_run_bat_strips_cmd_metacharacters(sales_submission):
    risky = sales_submission.model_copy(deep=True)
    risky.spec.title = "Sales & returns > report | 100%"
    with zipfile.ZipFile(io.BytesIO(_zip(risky))) as zf:
        bat = zf.read("run.bat").decode()
    title_line = next(line for line in bat.splitlines() if "Sales" in line)
    assert not any(ch in title_line for ch in "&|>%")
