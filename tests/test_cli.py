import subprocess
import textwrap

from perf_lint.analysis import HIGH, MED, UNKNOWN, Finding
from perf_lint.cli import _exit_code, run
from perf_lint.config import load_config
from perf_lint.gitdiff import new_findings

QUADRATIC = textwrap.dedent("""
    def find_dupes(items):
        for a in items:
            for b in items:
                pass
""")


def test_suppression_comment_on_line(tmp_path):
    src = textwrap.dedent("""
        def find_dupes(items):
            for a in items:
                for b in items:  # perf-lint: ignore
                    pass
    """)
    (tmp_path / "mod.py").write_text(src)
    findings, _ = run([str(tmp_path)])
    assert findings == []


def test_suppression_comment_on_line_above(tmp_path):
    src = textwrap.dedent("""
        def find_dupes(items):
            for a in items:
                # perf-lint: ignore
                for b in items:
                    pass
    """)
    (tmp_path / "mod.py").write_text(src)
    findings, _ = run([str(tmp_path)])
    assert findings == []


def test_unsuppressed_still_flagged(tmp_path):
    (tmp_path / "mod.py").write_text(QUADRATIC)
    findings, _ = run([str(tmp_path)])
    assert len(findings) == 1


def test_exclude_globs(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "mod.py").write_text(QUADRATIC)
    findings, _ = run([str(tmp_path)], exclude=["*/tests/*"])
    assert findings == []


def test_config_loading(tmp_path):
    (tmp_path / ".perf-lint.toml").write_text(
        'exclude = ["*/vendor/*"]\nfail_on = "high"\nllm_model = "m1"\n'
    )
    config = load_config([str(tmp_path)])
    assert config.exclude == ["*/vendor/*"]
    assert config.fail_on == "high"
    assert config.llm_model == "m1"


def test_exit_codes():
    high = Finding("f", 1, "g", HIGH, "O(n^2)", "")
    med = Finding("f", 1, "g", MED, "O(n*m)", "")
    unknown = Finding("f", 1, "g", UNKNOWN, "?", "")
    assert _exit_code([med], "med") == 1
    assert _exit_code([med], "high") == 0
    assert _exit_code([high], "high") == 1
    assert _exit_code([high, med], "never") == 0
    assert _exit_code([unknown], "med") == 0


def test_diff_mode_reports_only_new_findings(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True
        )

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    old = textwrap.dedent("""
        def old_quadratic(items):
            for a in items:
                for b in items:
                    pass
    """)
    (repo / "mod.py").write_text(old)
    git("add", "-A")
    git("commit", "-q", "-m", "baseline")

    (repo / "mod.py").write_text(old + textwrap.dedent("""
        def new_quadratic(users):
            for a in users:
                for b in users:
                    pass
    """))
    findings, _ = run([str(repo)])
    assert len(findings) == 2  # both present in working tree
    new = new_findings("HEAD", [str(repo)], findings, lambda p: run(p))
    assert [f.function for f in new] == ["new_quadratic"]
