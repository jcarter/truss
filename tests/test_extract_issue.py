import pytest
from unittest.mock import patch, Mock

from truss.extract_issue import detect_source, Source, main


class TestDetectSource:
    def test_sentry_url_standard(self):
        assert detect_source("https://myorg.sentry.io/issues/12345/") == Source.SENTRY

    def test_sentry_url_no_trailing_slash(self):
        assert detect_source("https://myorg.sentry.io/issues/12345") == Source.SENTRY

    def test_sentry_url_with_query_params(self):
        assert detect_source("https://myorg.sentry.io/issues/12345/?query=is%3Aunresolved") == Source.SENTRY

    def test_sentry_url_with_org_path(self):
        assert detect_source("https://myorg.sentry.io/organizations/myorg/issues/12345/") == Source.SENTRY

    def test_jira_url(self):
        assert detect_source("https://myorg.atlassian.net/browse/PROJ-123") == Source.JIRA

    def test_jira_url_custom_domain(self):
        assert detect_source("https://jira.mycompany.com/browse/TEAM-42") == Source.JIRA

    def test_jira_url_with_query_params(self):
        assert detect_source("https://myorg.atlassian.net/browse/PROJ-123?focusedId=456") == Source.JIRA

    def test_jira_bare_ticket_key(self):
        assert detect_source("PROJ-123") == Source.JIRA

    def test_jira_bare_ticket_underscore(self):
        assert detect_source("MY_PROJ-99") == Source.JIRA

    def test_unknown_url(self):
        assert detect_source("https://example.com/issues/123") == Source.UNKNOWN

    def test_random_string(self):
        assert detect_source("not-a-ticket") == Source.UNKNOWN

    def test_empty_string(self):
        assert detect_source("") == Source.UNKNOWN


class TestMainCLI:
    def test_sentry_dispatch(self, monkeypatch, capsys):
        monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
        with patch("truss.extract_issue.run_sentry", return_value="sentry output") as mock_run:
            with patch("sys.argv", ["extract-issue", "https://myorg.sentry.io/issues/12345/"]):
                main()
        mock_run.assert_called_once_with("https://myorg.sentry.io/issues/12345/", "markdown")
        captured = capsys.readouterr()
        assert "sentry output" in captured.out

    def test_jira_dispatch_url(self, monkeypatch, capsys):
        monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
        with patch("truss.extract_issue.run_jira", return_value="jira output") as mock_run:
            with patch("sys.argv", ["extract-issue", "https://myorg.atlassian.net/browse/PROJ-123"]):
                main()
        mock_run.assert_called_once_with("https://myorg.atlassian.net/browse/PROJ-123", "markdown")
        captured = capsys.readouterr()
        assert "jira output" in captured.out

    def test_jira_dispatch_ticket_key(self, monkeypatch, capsys):
        monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
        with patch("truss.extract_issue.run_jira", return_value="jira output") as mock_run:
            with patch("sys.argv", ["extract-issue", "PROJ-123"]):
                main()
        mock_run.assert_called_once_with("PROJ-123", "markdown")

    def test_format_flag_passed_through(self, monkeypatch, capsys):
        monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")
        with patch("truss.extract_issue.run_sentry", return_value="json output") as mock_run:
            with patch("sys.argv", ["extract-issue", "https://myorg.sentry.io/issues/12345/", "--format", "json"]):
                main()
        mock_run.assert_called_once_with("https://myorg.sentry.io/issues/12345/", "json")

    def test_unknown_input_exits(self, capsys):
        with patch("sys.argv", ["extract-issue", "not-a-valid-input"]):
            with pytest.raises(SystemExit):
                main()
        captured = capsys.readouterr()
        assert "Could not determine" in captured.err
