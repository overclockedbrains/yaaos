"""Tests for the filter component."""

from __future__ import annotations

from pathlib import Path
from yaaos_sfs.filter import FileFilter


def test_directory_skip_list(tmp_path: Path):
    filter = FileFilter(tmp_path, [".py"], max_file_size_mb=10)
    for hidden_dir in [".git", "node_modules", "venv", "__pycache__"]:
        d = tmp_path / hidden_dir
        d.mkdir()
        assert filter.is_dir_allowed(d) is False

        # also test a hypothetical file inside
        f = d / "test.py"
        assert filter.should_index(f, file_size=10) is False


def test_gitignore_patterns_are_respected(tmp_path: Path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.log\nbuild/\n")

    filter = FileFilter(tmp_path, [".log", ".txt", ".py"], max_file_size_mb=10)

    f1 = tmp_path / "test.log"
    f1.write_text("logs")
    assert filter.should_index(f1, file_size=4) is False

    # build directory
    d = tmp_path / "build"
    d.mkdir()
    assert filter.is_dir_allowed(d) is False

    # normal file should be allowed
    f2 = tmp_path / "main.py"
    f2.write_text("code")
    assert filter.should_index(f2, file_size=4) is True


def test_extension_whitelist_filters_correctly(tmp_path: Path):
    filter = FileFilter(tmp_path, [".py", ".rs"], max_file_size_mb=10)

    f1 = tmp_path / "test.py"
    f1.write_text("code")
    assert filter.should_index(f1, file_size=4) is True

    f2 = tmp_path / "test.txt"
    f2.write_text("text")
    assert filter.should_index(f2, file_size=4) is False


def test_file_size_limit_works(tmp_path: Path):
    # 1 MB limit
    filter = FileFilter(tmp_path, [".py"], max_file_size_mb=1.0)

    f1 = tmp_path / "small.py"
    f1.write_text("small")
    assert filter.should_index(f1, file_size=100) is True

    f2 = tmp_path / "large.py"
    f2.write_text("large")
    assert filter.should_index(f2, file_size=2 * 1024 * 1024) is False

    # empty file skipped
    f3 = tmp_path / "empty.py"
    f3.touch()
    assert filter.should_index(f3, file_size=0) is False


def test_sfsignore_patterns_are_respected(tmp_path: Path):
    sfsignore = tmp_path / ".sfsignore"
    sfsignore.write_text("*.generated.py\nsecrets/\n")

    filter = FileFilter(tmp_path, [".py", ".txt"], max_file_size_mb=10)

    f1 = tmp_path / "output.generated.py"
    f1.write_text("generated code")
    assert filter.should_index(f1, file_size=14) is False

    # Normal file should still be allowed
    f2 = tmp_path / "main.py"
    f2.write_text("code")
    assert filter.should_index(f2, file_size=4) is True

    # secrets directory
    d = tmp_path / "secrets"
    d.mkdir()
    assert filter.is_dir_allowed(d) is False


def test_dot_dirs_not_in_ignorelist_are_allowed(tmp_path: Path):
    """Directories like .github or .planning should NOT be skipped."""
    filter = FileFilter(tmp_path, [".py", ".md"], max_file_size_mb=10)

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    assert filter.is_dir_allowed(github_dir) is True

    planning_dir = tmp_path / ".planning"
    planning_dir.mkdir()
    assert filter.is_dir_allowed(planning_dir) is True


def test_hidden_files_are_skipped(tmp_path: Path):
    filter = FileFilter(tmp_path, [".py", ".env"], max_file_size_mb=10)

    f1 = tmp_path / ".env"
    f1.write_text("secret")
    assert filter.should_index(f1, file_size=6) is False

    f2 = tmp_path / ".hidden.py"
    f2.write_text("hidden")
    assert filter.should_index(f2, file_size=6) is False
