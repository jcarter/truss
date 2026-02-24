import json
import pytest
from unittest.mock import patch, Mock

from truss.jira_extractor import (
    JiraError,
    parse_ticket_input,
    load_config,
    fetch_ticket,
    format_markdown,
    format_json,
    format_plain,
    extract_text_from_adf,
    adf_to_markdown,
    _clean_markdown,
    _extract_custom_field,
    main,
)


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_returns_all_three_vars(monkeypatch):
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")

    config = load_config()
    assert config["url"] == "https://test.atlassian.net"
    assert config["email"] == "user@test.com"
    assert config["token"] == "test-token"


def test_load_config_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net/")
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")

    config = load_config()
    assert config["url"] == "https://test.atlassian.net"


def test_load_config_exits_on_missing_var(monkeypatch):
    monkeypatch.delenv("JIRA_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)

    with pytest.raises(JiraError):
        load_config()


# ---------------------------------------------------------------------------
# parse_ticket_input
# ---------------------------------------------------------------------------


def test_parse_ticket_input_plain_key():
    assert parse_ticket_input("PROJ-123") == "PROJ-123"


def test_parse_ticket_input_url():
    assert parse_ticket_input("https://myorg.atlassian.net/browse/PROJ-123") == "PROJ-123"


def test_parse_ticket_input_url_with_query_params():
    assert parse_ticket_input("https://myorg.atlassian.net/browse/PROJ-456?focusedId=12345") == "PROJ-456"


def test_parse_ticket_input_url_http():
    assert parse_ticket_input("http://myorg.atlassian.net/browse/PROJ-789") == "PROJ-789"


def test_parse_ticket_input_url_custom_domain():
    assert parse_ticket_input("https://jira.mycompany.com/browse/TEAM-42") == "TEAM-42"


def test_parse_ticket_input_url_underscore_project():
    assert parse_ticket_input("https://myorg.atlassian.net/browse/MY_PROJ-99") == "MY_PROJ-99"


def test_parse_ticket_input_invalid():
    with pytest.raises(ValueError, match="Could not parse Jira input"):
        parse_ticket_input("not-a-ticket")


def test_parse_ticket_input_invalid_url():
    with pytest.raises(ValueError, match="Could not parse Jira input"):
        parse_ticket_input("https://myorg.atlassian.net/issues/PROJ-123")


# ---------------------------------------------------------------------------
# fetch_ticket
# ---------------------------------------------------------------------------


def test_fetch_ticket_success():
    config = {
        "url": "https://test.atlassian.net",
        "email": "user@test.com",
        "token": "test-token",
    }
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "key": "PROJ-123",
        "fields": {
            "summary": "Test ticket",
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "A description"}]}]},
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Story"},
            "priority": {"name": "High"},
            "assignee": {"displayName": "Jane Doe"},
            "reporter": {"displayName": "John Smith"},
        },
    }

    with patch("truss.jira_extractor.requests.get", return_value=mock_response) as mock_get:
        result = fetch_ticket(config, "PROJ-123")

    mock_get.assert_called_once_with(
        "https://test.atlassian.net/rest/api/3/issue/PROJ-123",
        params={"fields": "summary,description,status,issuetype,priority,assignee,reporter,customfield_11271,customfield_11504"},
        auth=("user@test.com", "test-token"),
        timeout=30,
    )
    assert result["key"] == "PROJ-123"
    assert result["fields"]["summary"] == "Test ticket"


def test_fetch_ticket_not_found():
    config = {
        "url": "https://test.atlassian.net",
        "email": "user@test.com",
        "token": "test-token",
    }
    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = Exception("404")

    with patch("truss.jira_extractor.requests.get", return_value=mock_response):
        with pytest.raises(JiraError):
            fetch_ticket(config, "PROJ-999")


def test_fetch_ticket_auth_failure():
    config = {
        "url": "https://test.atlassian.net",
        "email": "user@test.com",
        "token": "bad-token",
    }
    mock_response = Mock()
    mock_response.status_code = 401
    mock_response.raise_for_status.side_effect = Exception("401")

    with patch("truss.jira_extractor.requests.get", return_value=mock_response):
        with pytest.raises(JiraError):
            fetch_ticket(config, "PROJ-123")


def test_fetch_ticket_connection_error():
    config = {
        "url": "https://test.atlassian.net",
        "email": "user@test.com",
        "token": "test-token",
    }
    import requests as req
    with patch("truss.jira_extractor.requests.get", side_effect=req.ConnectionError("refused")):
        with pytest.raises(JiraError, match="Could not connect"):
            fetch_ticket(config, "PROJ-123")


def test_fetch_ticket_server_error():
    config = {
        "url": "https://test.atlassian.net",
        "email": "user@test.com",
        "token": "test-token",
    }
    mock_response = Mock()
    mock_response.status_code = 500

    with patch("truss.jira_extractor.requests.get", return_value=mock_response):
        with pytest.raises(JiraError, match="500"):
            fetch_ticket(config, "PROJ-123")


# ---------------------------------------------------------------------------
# _extract_custom_field
# ---------------------------------------------------------------------------


def test_extract_custom_field_dict():
    assert _extract_custom_field({"value": "backend"}) == "backend"


def test_extract_custom_field_dict_name_fallback():
    assert _extract_custom_field({"name": "prod"}) == "prod"


def test_extract_custom_field_list():
    assert _extract_custom_field(["a", "b"]) == "a, b"


def test_extract_custom_field_other():
    assert _extract_custom_field(42) == "42"


# ---------------------------------------------------------------------------
# SAMPLE fixtures
# ---------------------------------------------------------------------------


SAMPLE_ISSUE = {
    "key": "PROJ-123",
    "fields": {
        "summary": "Fix login bug",
        "description": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Users cannot log in after password reset."}],
                }
            ],
        },
        "status": {"name": "In Progress"},
        "issuetype": {"name": "Bug"},
        "priority": {"name": "High"},
        "assignee": {"displayName": "Jane Doe"},
        "reporter": {"displayName": "John Smith"},
        "customfield_11271": "backend/api/auth.py",
        "customfield_11504": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Verify login works after password reset."}],
                }
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# extract_text_from_adf / adf_to_markdown
# ---------------------------------------------------------------------------


def test_extract_text_from_adf():
    adf = SAMPLE_ISSUE["fields"]["description"]
    assert extract_text_from_adf(adf) == "Users cannot log in after password reset."


def test_extract_text_from_adf_none():
    assert extract_text_from_adf(None) == "No description provided."


def test_adf_to_markdown_simple():
    adf = SAMPLE_ISSUE["fields"]["description"]
    result = adf_to_markdown(adf)
    assert "Users cannot log in after password reset." in result


def test_adf_to_markdown_none():
    assert adf_to_markdown(None) == "No description provided."


def test_adf_to_markdown_with_bold():
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "This is "},
                    {"type": "text", "text": "bold", "marks": [{"type": "strong"}]},
                    {"type": "text", "text": " text."},
                ],
            },
        ],
    }
    result = adf_to_markdown(adf)
    assert "**bold**" in result
    assert "This is **bold** text." in result


def test_adf_to_markdown_strips_br_tags():
    """The library inserts <br> in table cells -- we strip them."""
    dirty = "| Name<br> | Value<br> |\n| --- | --- |\n| foo<br> | bar<br> |"
    result = _clean_markdown(dirty)
    assert "<br>" not in result
    assert "| Name | Value |" in result
    assert "| foo | bar |" in result


def test_clean_markdown_inserts_separator_for_headerless_table():
    """Tables without a separator row get one injected."""
    headerless = "| foo | bar |\n| baz | qux |"
    result = _clean_markdown(headerless)
    lines = result.split("\n")
    assert lines[0] == "| foo | bar |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| baz | qux |"


def test_clean_markdown_preserves_existing_separator():
    """Tables that already have a separator row are not double-separated."""
    with_sep = "| Name | Value |\n| --- | --- |\n| foo | bar |"
    result = _clean_markdown(with_sep)
    assert result.count("| --- | --- |") == 1


def test_adf_to_markdown_falls_back_on_unsupported_nodes():
    """When the library can't parse a node, we fall back to plain text extraction."""
    adf = {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "Steps"}],
            },
            {
                "type": "orderedList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Step one"}],
                            }
                        ],
                    },
                ],
            },
        ],
    }
    result = adf_to_markdown(adf)
    # Falls back to plain text -- still extracts the content
    assert "Steps" in result
    assert "Step one" in result


# ---------------------------------------------------------------------------
# format_markdown / format_json / format_plain
# ---------------------------------------------------------------------------


def test_format_markdown():
    result = format_markdown(SAMPLE_ISSUE)
    assert "# PROJ-123: Fix login bug" in result
    assert "| Status" in result
    assert "In Progress" in result
    assert "Bug" in result
    assert "High" in result
    assert "Jane Doe" in result
    assert "John Smith" in result
    assert "Code/Config" in result
    assert "backend/api/auth.py" in result
    assert "## Description" in result
    assert "Users cannot log in after password reset." in result
    assert "## Acceptance Criteria / Test Cases" in result
    assert "Verify login works after password reset." in result


def test_format_markdown_unassigned():
    issue = {
        "key": "PROJ-456",
        "fields": {
            "summary": "Unassigned ticket",
            "description": None,
            "status": {"name": "Open"},
            "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"},
            "assignee": None,
            "reporter": {"displayName": "John Smith"},
        },
    }
    result = format_markdown(issue)
    assert "Unassigned" in result
    assert "No description provided." in result
    assert "TBD" in result
    assert "## Acceptance Criteria / Test Cases" not in result


def test_format_json():
    result = format_json(SAMPLE_ISSUE)
    parsed = json.loads(result)
    assert parsed["key"] == "PROJ-123"
    assert parsed["summary"] == "Fix login bug"
    assert parsed["status"] == "In Progress"
    assert parsed["type"] == "Bug"
    assert parsed["priority"] == "High"
    assert parsed["assignee"] == "Jane Doe"
    assert parsed["reporter"] == "John Smith"
    assert parsed["code_config"] == "backend/api/auth.py"
    assert parsed["description"] == "Users cannot log in after password reset."
    assert parsed["acceptance_criteria"] == "Verify login works after password reset."


def test_format_plain():
    result = format_plain(SAMPLE_ISSUE)
    assert "PROJ-123: Fix login bug" in result
    assert "# " not in result
    assert "| " not in result
    assert "Status:      In Progress" in result
    assert "Type:        Bug" in result
    assert "Priority:    High" in result
    assert "Assignee:    Jane Doe" in result
    assert "Reporter:    John Smith" in result
    assert "Code/Config: backend/api/auth.py" in result
    assert "Description:" in result
    assert "Users cannot log in after password reset." in result
    assert "Acceptance Criteria / Test Cases:" in result
    assert "Verify login works after password reset." in result


def test_format_plain_acceptance_criteria_string():
    """When acceptance criteria is a plain string (not ADF), it should still render."""
    issue = {
        "key": "PROJ-789",
        "fields": {
            "summary": "String AC ticket",
            "description": None,
            "status": {"name": "Open"},
            "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"},
            "assignee": None,
            "reporter": {"displayName": "John Smith"},
            "customfield_11504": "Must pass all unit tests.",
        },
    }
    result = format_plain(issue)
    assert "Acceptance Criteria / Test Cases:" in result
    assert "Must pass all unit tests." in result


def test_format_plain_unassigned():
    issue = {
        "key": "PROJ-456",
        "fields": {
            "summary": "Unassigned ticket",
            "description": None,
            "status": {"name": "Open"},
            "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"},
            "assignee": None,
            "reporter": {"displayName": "John Smith"},
        },
    }
    result = format_plain(issue)
    assert "Assignee:    Unassigned" in result
    assert "Code/Config: TBD" in result
    assert "No description provided." in result
    assert "Acceptance Criteria / Test Cases:" not in result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_markdown_output(monkeypatch, capsys):
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")

    with patch("truss.jira_extractor.fetch_ticket", return_value=SAMPLE_ISSUE):
        with patch("sys.argv", ["jira-extractor.py", "PROJ-123"]):
            main()

    captured = capsys.readouterr()
    assert "# PROJ-123: Fix login bug" in captured.out


def test_main_json_output(monkeypatch, capsys):
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")

    with patch("truss.jira_extractor.fetch_ticket", return_value=SAMPLE_ISSUE):
        with patch("sys.argv", ["jira-extractor.py", "PROJ-123", "--format", "json"]):
            main()

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["key"] == "PROJ-123"


def test_main_plain_output(monkeypatch, capsys):
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")

    with patch("truss.jira_extractor.fetch_ticket", return_value=SAMPLE_ISSUE):
        with patch("sys.argv", ["jira-extractor.py", "PROJ-123", "--format", "plain"]):
            main()

    captured = capsys.readouterr()
    assert "PROJ-123: Fix login bug" in captured.out
    assert "# " not in captured.out


def test_main_accepts_jira_url(monkeypatch, capsys):
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")

    with patch("truss.jira_extractor.fetch_ticket", return_value=SAMPLE_ISSUE) as mock_fetch:
        with patch("sys.argv", ["jira_extractor.py", "https://myorg.atlassian.net/browse/PROJ-123"]):
            main()

    mock_fetch.assert_called_once()
    assert mock_fetch.call_args[0][1] == "PROJ-123"
    captured = capsys.readouterr()
    assert "# PROJ-123: Fix login bug" in captured.out


def test_main_rejects_invalid_input(monkeypatch):
    monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "user@test.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token")

    with patch("sys.argv", ["jira_extractor.py", "not-valid"]):
        with pytest.raises(SystemExit):
            main()
