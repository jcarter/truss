import pytest
from unittest.mock import patch, Mock

from truss.extract_issue import detect_source, Source, main, run_sentry, run_jira
from truss.sentry_extractor import SentryError
from truss.jira_extractor import JiraError


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

    def test_main_sentry_error_exits(self, capsys):
        with patch("truss.extract_issue.run_sentry", side_effect=SentryError("boom")):
            with patch("sys.argv", ["extract-issue", "https://myorg.sentry.io/issues/12345/"]):
                with pytest.raises(SystemExit):
                    main()
        captured = capsys.readouterr()
        assert "boom" in captured.err

    def test_main_jira_error_exits(self, capsys):
        with patch("truss.extract_issue.run_jira", side_effect=JiraError("kaboom")):
            with patch("sys.argv", ["extract-issue", "PROJ-123"]):
                with pytest.raises(SystemExit):
                    main()
        captured = capsys.readouterr()
        assert "kaboom" in captured.err


class TestRunSentry:
    SENTRY_URL = "https://myorg.sentry.io/issues/12345/"

    def _mock_run_sentry(self, output_format, expected_output):
        with patch("truss.extract_issue.parse_issue_url", return_value=("myorg", "12345")):
            with patch("truss.extract_issue.sentry_load_config", return_value={"token": "t"}):
                with patch("truss.extract_issue.sentry_fetch_issue", return_value={"tags": []}):
                    with patch("truss.extract_issue.fetch_latest_event", return_value=None):
                        with patch("truss.extract_issue.fetch_tag_details", return_value=[]):
                            with patch(f"truss.extract_issue.sentry_format_{output_format}", return_value=expected_output):
                                return run_sentry(self.SENTRY_URL, output_format)

    def test_run_sentry_markdown(self):
        result = self._mock_run_sentry("markdown", "# title")
        assert result == "# title"

    def test_run_sentry_json(self):
        result = self._mock_run_sentry("json", '{"title": "x"}')
        assert result == '{"title": "x"}'

    def test_run_sentry_plain(self):
        result = self._mock_run_sentry("plain", "title\nLevel: error")
        assert result == "title\nLevel: error"


class TestRunJira:
    JIRA_URL = "https://myorg.atlassian.net/browse/PROJ-123"
    SAMPLE_ISSUE = {
        "key": "PROJ-123",
        "fields": {
            "summary": "Fix login bug",
            "description": None,
            "status": {"name": "Open"},
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "assignee": None,
            "reporter": {"displayName": "Jane"},
        },
    }

    def _mock_run_jira(self, output_format, expected_output):
        with patch("truss.extract_issue.parse_ticket_input", return_value="PROJ-123"):
            with patch("truss.extract_issue.jira_load_config", return_value={"url": "x", "email": "e", "token": "t"}):
                with patch("truss.extract_issue.fetch_ticket", return_value=self.SAMPLE_ISSUE):
                    with patch(f"truss.extract_issue.jira_format_{output_format}", return_value=expected_output):
                        return run_jira(self.JIRA_URL, output_format)

    def test_run_jira_markdown(self):
        result = self._mock_run_jira("markdown", "# PROJ-123: Fix login bug")
        assert result == "# PROJ-123: Fix login bug"

    def test_run_jira_json(self):
        result = self._mock_run_jira("json", '{"key": "PROJ-123"}')
        assert result == '{"key": "PROJ-123"}'

    def test_run_jira_plain(self):
        result = self._mock_run_jira("plain", "PROJ-123: Fix login bug")
        assert result == "PROJ-123: Fix login bug"
