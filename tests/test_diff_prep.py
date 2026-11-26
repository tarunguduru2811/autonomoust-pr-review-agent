"""Tests for diff preparation, lockfile filtering, and repo rule loading."""

from pathlib import Path

from pr_agent.orchestrator.diff_prep import is_ignored_file, load_repo_rules, prepare_diff

MULTI_FILE_DIFF = """diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,2 +1,2 @@
-old
+new
diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 def main():
+    print("hi")
     pass
"""


def test_is_ignored_file_filters_lockfiles_and_build_dirs() -> None:
    assert is_ignored_file("package-lock.json") is True
    assert is_ignored_file("dist/bundle.min.js") is True
    assert is_ignored_file("src/app.py") is False
    assert is_ignored_file("legacy/old.py", extra_ignore_patterns=["legacy/*"]) is True


def test_prepare_diff_strips_ignored_files() -> None:
    prepared = prepare_diff(MULTI_FILE_DIFF)
    assert prepared.skipped_files == ["package-lock.json"]
    assert len(prepared.files) == 1
    assert prepared.files[0].path == "src/app.py"
    assert prepared.files[0].added_lines == 1
    assert "package-lock.json" not in prepared.cleaned_diff


def test_load_repo_rules_reads_config_files(tmp_path: Path) -> None:
    (tmp_path / ".pr-agent.yaml").write_text("rules:\n  - Use safeQuery()\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Rules\nBe concise.", encoding="utf-8")
    rules = load_repo_rules(tmp_path)
    assert "Use safeQuery()" in rules
    assert "Be concise." in rules

