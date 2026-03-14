"""Tests for the CLI tool."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from yaaos_sfs.cli import main


class TestCLI:
    def test_no_args_shows_help(self):
        runner = CliRunner()
        result = runner.invoke(main, [])
        assert result.exit_code == 0
        assert "yaaos-find" in result.output.lower() or "search" in result.output.lower()

    def test_status_flag(self):
        runner = CliRunner()
        with patch("yaaos_sfs.cli._show_status") as mock_status:
            runner.invoke(main, ["--status"])
            mock_status.assert_called_once()

    def test_search_invokes_search(self):
        runner = CliRunner()
        with patch("yaaos_sfs.cli._do_search") as mock_search:
            runner.invoke(main, ["test query"])
            mock_search.assert_called_once()

    def test_type_filter_passed(self):
        runner = CliRunner()
        with patch("yaaos_sfs.cli._do_search") as mock_search:
            result = runner.invoke(main, ["--type", "py", "search query"])
            assert result.exit_code == 0, f"CLI error: {result.output}"
            mock_search.assert_called_once()
            # _do_search(config, query, top, file_type, snippets)
            assert mock_search.call_args[0][3] == "py"

    def test_top_n_option(self):
        runner = CliRunner()
        with patch("yaaos_sfs.cli._do_search") as mock_search:
            result = runner.invoke(main, ["--top", "5", "search query"])
            assert result.exit_code == 0, f"CLI error: {result.output}"
            mock_search.assert_called_once()
            assert mock_search.call_args[0][2] == 5
