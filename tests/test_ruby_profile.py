"""Unit tests for src/evaluator/languages/ruby.py."""

import pytest
from unittest.mock import patch, MagicMock

from src.evaluator.languages.ruby import (
    RubyProfile,
    RubocopProfile,
    _check_ruby_test,
    _check_rspec_desc,
    _check_minitest_summary,
)


# ── _check_rspec_desc ─────────────────────────────────────────────────────────

class TestCheckRspecDesc:
    def test_passing_description_line(self):
        output = (
            "RuboCop::CLI\n"
            "  returns 0 if there are no offenses shown\n"
            "  checks a given file and returns 1\n"
            "\n"
            "2 examples, 0 failures\n"
        )
        assert _check_rspec_desc(output, "returns 0 if there are no offenses shown") is True

    def test_failed_description_with_marker(self):
        output = (
            "RuboCop::CLI\n"
            "  returns 0 if there are no offenses shown (FAILED - 1)\n"
            "  checks a given file and returns 1\n"
            "\n"
            "2 examples, 1 failure\n"
        )
        assert _check_rspec_desc(output, "returns 0 if there are no offenses shown") is False

    def test_other_test_failed_specific_passed(self):
        output = (
            "RuboCop::CLI\n"
            "  returns 0 if there are no offenses shown\n"
            "  checks a given file and returns 1 (FAILED - 1)\n"
            "\n"
            "2 examples, 1 failure\n"
        )
        # The specific test passed (no FAILED marker on its line)
        assert _check_rspec_desc(output, "returns 0 if there are no offenses shown") is True

    def test_summary_fallback_zero_examples(self):
        output = "0 examples, 0 failures\n"
        assert _check_rspec_desc(output, "some description") is False

    def test_errors_outside_examples(self):
        output = "2 errors occurred outside of examples\n"
        assert _check_rspec_desc(output, "some description") is False

    def test_backtick_description(self):
        desc = "`Lint/Syntax` must be enabled when `DisabledByDefault: true`"
        output = (
            "  `Lint/Syntax` must be enabled when `DisabledByDefault: true`\n"
            "2 examples, 0 failures\n"
        )
        assert _check_rspec_desc(output, desc) is True

    def test_backtick_description_failed(self):
        desc = "`Lint/Syntax` must be enabled when `DisabledByDefault: true`"
        output = (
            "  `Lint/Syntax` must be enabled when `DisabledByDefault: true` (FAILED - 1)\n"
            "2 examples, 1 failure\n"
        )
        assert _check_rspec_desc(output, desc) is False


# ── _check_minitest_summary ───────────────────────────────────────────────────

class TestCheckMinitestSummary:
    def test_passing_single_run(self):
        output = "1 runs, 3 assertions, 0 failures, 0 errors, 0 skips\n"
        assert _check_minitest_summary(output) is True

    def test_passing_multiple_runs(self):
        output = "11 runs, 33 assertions, 0 failures, 0 errors, 0 skips\n"
        assert _check_minitest_summary(output) is True

    def test_failing_run(self):
        output = "1 runs, 3 assertions, 1 failures, 0 errors, 0 skips\n"
        assert _check_minitest_summary(output) is False

    def test_error_run(self):
        output = "1 runs, 0 assertions, 0 failures, 1 errors, 0 skips\n"
        assert _check_minitest_summary(output) is False

    def test_zero_runs(self):
        output = "0 runs, 0 assertions, 0 failures, 0 errors, 0 skips\n"
        assert _check_minitest_summary(output) is False

    def test_minitest_reporters_format_pass(self):
        # minitest-reporters uses "N tests" not "N runs"
        output = "1 tests, 2 assertions, 0 failures, 0 errors, 0 pendings, 0 omissions, 0 notifications\n100% passed\n"
        assert _check_minitest_summary(output) is True

    def test_minitest_reporters_format_fail(self):
        output = "15 tests, 172 assertions, 1 failures, 1 errors, 0 pendings\n86.6667% passed\n"
        assert _check_minitest_summary(output) is False

    def test_minitest_reporters_zero_tests(self):
        output = "0 tests, 0 assertions, 0 failures, 0 errors\n"
        assert _check_minitest_summary(output) is False

    def test_no_summary(self):
        output = "some error output\nno tests ran\n"
        assert _check_minitest_summary(output) is False

    def test_errors_outside_examples(self):
        output = "1 errors occurred outside of examples\n"
        assert _check_minitest_summary(output) is False


# ── _check_ruby_test dispatch ─────────────────────────────────────────────────

class TestCheckRubyTestDispatch:
    def test_rspec_file_path_format_pass(self):
        output = (
            "  should detect iPhone 13 in portrait and landscape based on priority\n"
            "12 examples, 0 failures\n"
        )
        name = "should detect iPhone 13 in portrait and landscape based on priority - ./frameit/spec/device_spec.rb[1:1:1:2]"
        assert _check_ruby_test(output, name) is True

    def test_rspec_file_path_format_fail(self):
        output = (
            "  should detect iPhone 13 in portrait and landscape based on priority (FAILED - 1)\n"
            "12 examples, 1 failure\n"
        )
        name = "should detect iPhone 13 in portrait and landscape based on priority - ./frameit/spec/device_spec.rb[1:1:1:2]"
        assert _check_ruby_test(output, name) is False

    def test_minitest_class_method_pass(self):
        output = "11 runs, 33 assertions, 0 failures, 0 errors, 0 skips\n"
        name = "TestFilters#test_: filters where_exp filter should filter objects across multiple conditions"
        assert _check_ruby_test(output, name) is True

    def test_minitest_class_method_fail(self):
        output = "1 runs, 3 assertions, 1 failures, 0 errors, 0 skips\n"
        name = "TestFilters#test_: filters where_exp filter should filter objects across multiple conditions"
        assert _check_ruby_test(output, name) is False

    def test_bare_minitest_method_pass(self):
        output = "1 runs, 3 assertions, 0 failures, 0 errors, 0 skips\n"
        assert _check_ruby_test(output, "test_password") is True

    def test_bare_minitest_method_fail(self):
        output = "1 runs, 3 assertions, 1 failures, 0 errors, 0 skips\n"
        assert _check_ruby_test(output, "test_password") is False

    def test_bare_minitest_enoent_pass(self):
        output = "1 runs, 5 assertions, 0 failures, 0 errors, 0 skips\n"
        assert _check_ruby_test(output, "test_ENOENT_error_after_setup_watcher") is True

    def test_rspec_description_pass(self):
        output = "  returns 0 if there are no offenses shown\n1 example, 0 failures\n"
        assert _check_ruby_test(output, "returns 0 if there are no offenses shown") is True

    def test_rspec_description_fail(self):
        output = "  returns 0 if there are no offenses shown (FAILED - 1)\n1 example, 1 failure\n"
        assert _check_ruby_test(output, "returns 0 if there are no offenses shown") is False


# ── RubyProfile.build_test_command routing ────────────────────────────────────

class TestRubyProfileBuildTestCommand:
    def setup_method(self):
        self.profile = RubyProfile()
        self.task = MagicMock()
        self.container = "abc123"

    def test_rspec_file_path_routes_to_rspec(self):
        names = ["desc - ./spec/foo_spec.rb[1:1:1]"]
        cmd = self.profile.build_test_command(names, self.task, self.container)
        assert "bundle exec rspec" in cmd
        assert "--format documentation" in cmd

    def test_minitest_class_method_routes_to_helper(self):
        names = ["TestFilters#test_: some description"]
        with patch("src.evaluator.languages.ruby._find_ruby_file", return_value="/testbed/test/test_filters.rb"):
            cmd = self.profile.build_test_command(names, self.task, self.container)
        assert "bundle exec ruby" in cmd
        assert "-Ilib -Itest" in cmd
        assert "/testbed/test/test_filters.rb" in cmd

    def test_bare_minitest_routes_to_helper(self):
        names = ["test_password"]
        with patch("src.evaluator.languages.ruby._find_ruby_file", return_value="/testbed/test/internet_test.rb"):
            cmd = self.profile.build_test_command(names, self.task, self.container)
        assert "bundle exec ruby" in cmd
        assert "test_password" in cmd

    def test_bare_minitest_fallback_when_file_not_found(self):
        names = ["test_password"]
        with patch("src.evaluator.languages.ruby._find_ruby_file", return_value=None):
            cmd = self.profile.build_test_command(names, self.task, self.container)
        assert "bundle exec rake test" in cmd

    def test_pure_description_routes_to_rspec_spec(self):
        names = ["returns 0 if there are no offenses shown"]
        cmd = self.profile.build_test_command(names, self.task, self.container)
        assert "bundle exec rspec spec/" in cmd

    def test_empty_names(self):
        cmd = self.profile.build_test_command([], self.task, self.container)
        assert cmd == "echo 'no tests'"


class TestRubocopProfileBuildTestCommand:
    def setup_method(self):
        self.profile = RubocopProfile()
        self.task = MagicMock()

    def test_always_uses_rspec_spec(self):
        names = ["returns 0 if there are no offenses shown"]
        cmd = self.profile.build_test_command(names, self.task, "container")
        assert "bundle exec rspec spec/" in cmd

    def test_rspec_format_names_still_use_rspec_spec(self):
        # Even if names look like spec paths, RubocopProfile always uses spec/
        names = ["desc - ./spec/foo_spec.rb[1:1]"]
        cmd = self.profile.build_test_command(names, self.task, "container")
        assert "bundle exec rspec spec/" in cmd
