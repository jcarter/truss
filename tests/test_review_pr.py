import json
import subprocess

import pytest
from unittest.mock import patch, Mock, call

from truss.review_pr import (
    ReviewPRError,
    parse_pr_url,
    run_gh,
    fetch_pr_metadata,
    fetch_pr_diff,
    checkout_pr,
    find_jira_keys,
    find_sentry_urls,
    gather_context,
    build_prompt,
    main,
)
from truss.jira_extractor import JiraError
from truss.sentry_extractor import SentryError


class TestParsePRUrl:
    def test_standard_url(self):
        assert parse_pr_url("https://github.com/acme/repo/pull/42") == ("acme", "repo", "42")

    def test_url_with_files_path(self):
        assert parse_pr_url("https://github.com/acme/repo/pull/99/files") == ("acme", "repo", "99")

    def test_url_with_query_params(self):
        assert parse_pr_url("https://github.com/acme/repo/pull/7?diff=unified") == ("acme", "repo", "7")

    def test_http_url(self):
        assert parse_pr_url("http://github.com/acme/repo/pull/1") == ("acme", "repo", "1")

    def test_hyphenated_owner_and_repo(self):
        assert parse_pr_url("https://github.com/my-org/my-repo/pull/5") == ("my-org", "my-repo", "5")

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pr_url("https://gitlab.com/acme/repo/merge_requests/1")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pr_url("")

    def test_not_a_url_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_pr_url("PROJ-123")


class TestRunGh:
    def test_success(self):
        mock_result = Mock(returncode=0, stdout="output\n", stderr="")
        with patch("truss.review_pr.subprocess.run", return_value=mock_result) as mock_run:
            assert run_gh(["pr", "view"]) == "output\n"
        mock_run.assert_called_once_with(
            ["gh", "pr", "view"],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_gh_not_installed(self):
        with patch("truss.review_pr.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(ReviewPRError, match="not installed"):
                run_gh(["pr", "view"])

    def test_timeout(self):
        with patch("truss.review_pr.subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 60)):
            with pytest.raises(ReviewPRError, match="timed out"):
                run_gh(["pr", "view"])

    def test_nonzero_exit(self):
        mock_result = Mock(returncode=1, stdout="", stderr="not found")
        with patch("truss.review_pr.subprocess.run", return_value=mock_result):
            with pytest.raises(ReviewPRError, match="not found"):
                run_gh(["pr", "view"])


class TestFetchPRMetadata:
    SAMPLE_METADATA = {
        "title": "Fix login bug",
        "body": "Fixes PROJ-123",
        "headRefName": "fix/PROJ-123-login",
        "baseRefName": "main",
        "author": {"login": "dev"},
        "number": 42,
        "url": "https://github.com/acme/repo/pull/42",
    }

    def test_returns_parsed_json(self):
        with patch("truss.review_pr.run_gh", return_value=json.dumps(self.SAMPLE_METADATA)):
            result = fetch_pr_metadata("https://github.com/acme/repo/pull/42")
        assert result["title"] == "Fix login bug"
        assert result["number"] == 42


class TestFetchPRDiff:
    def test_returns_diff_string(self):
        diff = "diff --git a/file.py b/file.py\n+new line\n"
        with patch("truss.review_pr.run_gh", return_value=diff):
            assert fetch_pr_diff("https://github.com/acme/repo/pull/42") == diff


class TestCheckoutPR:
    def test_calls_gh_checkout(self):
        with patch("truss.review_pr.run_gh") as mock_gh:
            checkout_pr("https://github.com/acme/repo/pull/42")
        mock_gh.assert_called_once_with(["pr", "checkout", "https://github.com/acme/repo/pull/42"])


class TestFindJiraKeys:
    def test_single_key_in_text(self):
        assert find_jira_keys("This fixes PROJ-123") == ["PROJ-123"]

    def test_multiple_keys(self):
        assert find_jira_keys("PROJ-1 and PROJ-2 are related") == ["PROJ-1", "PROJ-2"]

    def test_deduplication(self):
        assert find_jira_keys("PROJ-1 PROJ-1 PROJ-1") == ["PROJ-1"]

    def test_underscore_project(self):
        assert find_jira_keys("MY_PROJ-99") == ["MY_PROJ-99"]

    def test_key_in_branch_name(self):
        assert find_jira_keys("feature/PROJ-456-add-login") == ["PROJ-456"]

    def test_no_keys(self):
        assert find_jira_keys("no tickets here") == []

    def test_empty_string(self):
        assert find_jira_keys("") == []

    def test_none_input(self):
        assert find_jira_keys(None) == []

    def test_key_in_url(self):
        keys = find_jira_keys("https://myorg.atlassian.net/browse/PROJ-123")
        assert "PROJ-123" in keys


class TestFindSentryUrls:
    def test_standard_sentry_url(self):
        text = "See https://myorg.sentry.io/issues/12345 for details"
        assert find_sentry_urls(text) == ["https://myorg.sentry.io/issues/12345"]

    def test_org_path_url(self):
        text = "https://myorg.sentry.io/organizations/myorg/issues/99999"
        assert find_sentry_urls(text) == [text]

    def test_url_with_trailing_slash(self):
        urls = find_sentry_urls("https://myorg.sentry.io/issues/12345/")
        assert len(urls) == 1

    def test_multiple_urls_deduped(self):
        text = (
            "https://myorg.sentry.io/issues/111 and "
            "https://myorg.sentry.io/issues/111/"
        )
        assert len(find_sentry_urls(text)) == 1

    def test_different_urls_kept(self):
        text = (
            "https://myorg.sentry.io/issues/111 "
            "https://myorg.sentry.io/issues/222"
        )
        assert len(find_sentry_urls(text)) == 2

    def test_no_urls(self):
        assert find_sentry_urls("no sentry links") == []

    def test_empty_string(self):
        assert find_sentry_urls("") == []

    def test_none_input(self):
        assert find_sentry_urls(None) == []


class TestGatherContext:
    def test_jira_context_gathered(self):
        with patch("truss.review_pr.run_jira", return_value="# PROJ-1: Bug"):
            ctx = gather_context(["PROJ-1"], [])
        assert len(ctx["jira"]) == 1
        assert ctx["jira"][0] == ("PROJ-1", "# PROJ-1: Bug")

    def test_sentry_context_gathered(self):
        url = "https://myorg.sentry.io/issues/123"
        with patch("truss.review_pr.run_sentry", return_value="# Error"):
            ctx = gather_context([], [url])
        assert len(ctx["sentry"]) == 1
        assert ctx["sentry"][0] == (url, "# Error")

    def test_jira_error_skipped(self, capsys):
        with patch("truss.review_pr.run_jira", side_effect=JiraError("no creds")):
            ctx = gather_context(["PROJ-1"], [])
        assert ctx["jira"] == []
        assert "Warning" in capsys.readouterr().err

    def test_sentry_error_skipped(self, capsys):
        url = "https://myorg.sentry.io/issues/123"
        with patch("truss.review_pr.run_sentry", side_effect=SentryError("no token")):
            ctx = gather_context([], [url])
        assert ctx["sentry"] == []
        assert "Warning" in capsys.readouterr().err

    def test_sentry_value_error_skipped(self, capsys):
        with patch("truss.review_pr.run_sentry", side_effect=ValueError("bad url")):
            ctx = gather_context([], ["bad-url"])
        assert ctx["sentry"] == []
        assert "Warning" in capsys.readouterr().err

    def test_empty_inputs(self):
        ctx = gather_context([], [])
        assert ctx == {"jira": [], "sentry": []}


class TestBuildPrompt:
    METADATA = {
        "title": "Fix login bug",
        "body": "Resolves PROJ-123",
        "headRefName": "fix/login",
        "baseRefName": "main",
        "author": {"login": "dev"},
        "number": 42,
        "url": "https://github.com/acme/repo/pull/42",
    }
    DIFF = "diff --git a/login.py b/login.py\n+fixed\n"
    EMPTY_CONTEXT = {"jira": [], "sentry": []}

    def test_contains_pr_title(self):
        prompt = build_prompt(self.METADATA, self.DIFF, self.EMPTY_CONTEXT)
        assert "Fix login bug" in prompt

    def test_contains_author(self):
        prompt = build_prompt(self.METADATA, self.DIFF, self.EMPTY_CONTEXT)
        assert "dev" in prompt

    def test_contains_branch_info(self):
        prompt = build_prompt(self.METADATA, self.DIFF, self.EMPTY_CONTEXT)
        assert "fix/login" in prompt
        assert "main" in prompt

    def test_contains_diff(self):
        prompt = build_prompt(self.METADATA, self.DIFF, self.EMPTY_CONTEXT)
        assert self.DIFF in prompt

    def test_contains_review_instructions(self):
        prompt = build_prompt(self.METADATA, self.DIFF, self.EMPTY_CONTEXT)
        assert "Review Instructions" in prompt
        assert "gh api" in prompt

    def test_contains_pr_description(self):
        prompt = build_prompt(self.METADATA, self.DIFF, self.EMPTY_CONTEXT)
        assert "Resolves PROJ-123" in prompt

    def test_skips_empty_body(self):
        meta = {**self.METADATA, "body": ""}
        prompt = build_prompt(meta, self.DIFF, self.EMPTY_CONTEXT)
        assert "PR Description" not in prompt

    def test_skips_none_body(self):
        meta = {**self.METADATA, "body": None}
        prompt = build_prompt(meta, self.DIFF, self.EMPTY_CONTEXT)
        assert "PR Description" not in prompt

    def test_includes_jira_context(self):
        ctx = {"jira": [("PROJ-1", "# PROJ-1: Bug")], "sentry": []}
        prompt = build_prompt(self.METADATA, self.DIFF, ctx)
        assert "Jira: PROJ-1" in prompt
        assert "# PROJ-1: Bug" in prompt

    def test_includes_sentry_context(self):
        ctx = {"jira": [], "sentry": [("https://x.sentry.io/issues/1", "# Error")]}
        prompt = build_prompt(self.METADATA, self.DIFF, ctx)
        assert "Sentry Issue" in prompt
        assert "# Error" in prompt

    def test_no_linked_section_when_empty(self):
        prompt = build_prompt(self.METADATA, self.DIFF, self.EMPTY_CONTEXT)
        assert "Linked Issue Context" not in prompt


class TestMainCLI:
    PR_URL = "https://github.com/acme/repo/pull/42"
    METADATA = {
        "title": "Fix bug",
        "body": "Fixes PROJ-1",
        "headRefName": "fix/PROJ-1",
        "baseRefName": "main",
        "author": {"login": "dev"},
        "number": 42,
        "url": "https://github.com/acme/repo/pull/42",
    }

    def _run_main(self, argv, metadata=None, diff="diff\n", checkout=True):
        meta = metadata or self.METADATA
        patches = {
            "truss.review_pr.fetch_pr_metadata": meta,
            "truss.review_pr.fetch_pr_diff": diff,
            "truss.review_pr.gather_context": {"jira": [], "sentry": []},
        }
        with patch("sys.argv", ["review-pr"] + argv):
            with patch("truss.review_pr.checkout_pr") as mock_checkout:
                with patch("truss.review_pr.fetch_pr_metadata", return_value=patches["truss.review_pr.fetch_pr_metadata"]):
                    with patch("truss.review_pr.fetch_pr_diff", return_value=patches["truss.review_pr.fetch_pr_diff"]):
                        with patch("truss.review_pr.gather_context", return_value=patches["truss.review_pr.gather_context"]):
                            main()
        return mock_checkout

    def test_outputs_prompt(self, capsys):
        self._run_main([self.PR_URL])
        captured = capsys.readouterr()
        assert "Code Review" in captured.out
        assert "Fix bug" in captured.out

    def test_checkout_called(self):
        mock_checkout = self._run_main([self.PR_URL])
        mock_checkout.assert_called_once_with(self.PR_URL)

    def test_invalid_url_exits(self, capsys):
        with patch("sys.argv", ["review-pr", "not-a-pr-url"]):
            with pytest.raises(SystemExit):
                main()
        assert "Could not parse" in capsys.readouterr().err

    def test_gh_error_exits(self, capsys):
        with patch("sys.argv", ["review-pr", self.PR_URL]):
            with patch("truss.review_pr.parse_pr_url", return_value=("a", "b", "1")):
                with patch("truss.review_pr.checkout_pr", side_effect=ReviewPRError("gh failed")):
                    with pytest.raises(SystemExit):
                        main()
        assert "gh failed" in capsys.readouterr().err
