import json
import pytest
from unittest.mock import patch, Mock

from truss.sentry_extractor import (
    INCLUDED_TAGS,
    SentryError,
    parse_issue_url,
    load_config,
    fetch_issue,
    fetch_latest_event,
    fetch_tag_details,
    extract_message,
    extract_exceptions,
    extract_threads,
    extract_breadcrumbs,
    extract_request,
    extract_contexts,
    format_markdown,
    format_json,
    format_plain,
    main,
)
import truss.sentry_extractor as mod


def test_parse_issue_url_standard():
    org, issue_id = parse_issue_url("https://myorg.sentry.io/issues/12345/")
    assert org == "myorg"
    assert issue_id == "12345"


def test_parse_issue_url_no_trailing_slash():
    org, issue_id = parse_issue_url("https://myorg.sentry.io/issues/12345")
    assert org == "myorg"
    assert issue_id == "12345"


def test_parse_issue_url_with_query_params():
    org, issue_id = parse_issue_url("https://myorg.sentry.io/issues/12345/?query=is%3Aunresolved")
    assert org == "myorg"
    assert issue_id == "12345"


def test_parse_issue_url_with_project_path():
    org, issue_id = parse_issue_url("https://myorg.sentry.io/organizations/myorg/issues/12345/")
    assert org == "myorg"
    assert issue_id == "12345"


def test_parse_issue_url_invalid():
    with pytest.raises(ValueError):
        parse_issue_url("https://example.com/not-sentry")


def test_load_config_returns_token(monkeypatch):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token-abc")
    config = load_config()
    assert config["token"] == "test-token-abc"


def test_load_config_exits_on_missing_token(monkeypatch):
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    with pytest.raises(SentryError):
        load_config()


SAMPLE_ISSUE_RESPONSE = {
    "id": "12345",
    "title": "TypeError: Cannot read property 'foo' of undefined",
    "culprit": "app/views.py in home",
    "level": "error",
    "status": "unresolved",
    "count": "42",
    "userCount": 12,
    "firstSeen": "2026-02-15T10:00:00Z",
    "lastSeen": "2026-02-17T14:30:00Z",
    "permalink": "https://myorg.sentry.io/issues/12345/",
    "metadata": {"type": "TypeError", "value": "Cannot read property 'foo' of undefined"},
    "project": {"id": "1", "name": "my-project", "slug": "my-project"},
    "tags": [
        {"key": "environment", "name": "Environment", "totalValues": 30},
        {"key": "browser", "name": "Browser", "totalValues": 10},
    ],
}


def test_fetch_issue_success():
    config = {"token": "test-token"}
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_ISSUE_RESPONSE

    with patch.object(mod.requests, "get", return_value=mock_response) as mock_get:
        result = fetch_issue(config, "myorg", "12345")

    mock_get.assert_called_once_with(
        "https://sentry.io/api/0/organizations/myorg/issues/12345/",
        headers={"Authorization": "Bearer test-token"},
        timeout=30,
    )
    assert result["title"] == "TypeError: Cannot read property 'foo' of undefined"


def test_fetch_issue_not_found():
    config = {"token": "test-token"}
    mock_response = Mock()
    mock_response.status_code = 404

    with patch.object(mod.requests, "get", return_value=mock_response):
        with pytest.raises(SentryError):
            fetch_issue(config, "myorg", "99999")


def test_fetch_issue_auth_failure():
    config = {"token": "bad-token"}
    mock_response = Mock()
    mock_response.status_code = 401

    with patch.object(mod.requests, "get", return_value=mock_response):
        with pytest.raises(SentryError):
            fetch_issue(config, "myorg", "12345")


def test_fetch_issue_connection_error():
    config = {"token": "test-token"}
    with patch.object(mod.requests, "get", side_effect=mod.requests.ConnectionError("refused")):
        with pytest.raises(SentryError, match="Could not connect"):
            fetch_issue(config, "myorg", "12345")


def test_fetch_issue_server_error():
    config = {"token": "test-token"}
    mock_response = Mock()
    mock_response.status_code = 500

    with patch.object(mod.requests, "get", return_value=mock_response):
        with pytest.raises(SentryError, match="500"):
            fetch_issue(config, "myorg", "12345")


SAMPLE_EVENT_RESPONSE = {
    "eventID": "abc123",
    "id": "abc123",
    "groupID": "12345",
    "title": "TypeError: Cannot read property 'foo' of undefined",
    "message": "Cannot read property 'foo' of undefined",
    "platform": "python",
    "dateCreated": "2026-02-17T14:30:00Z",
    "tags": [
        {"key": "environment", "value": "production"},
        {"key": "level", "value": "error"},
    ],
    "entries": [
        {
            "type": "exception",
            "data": {
                "values": [
                    {
                        "type": "TypeError",
                        "value": "Cannot read property 'foo' of undefined",
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "app/models.py",
                                    "lineNo": 18,
                                    "function": "foo",
                                    "context": [
                                        [16, "    def foo(self):"],
                                        [17, "        # do something"],
                                        [18, "        return self.bar.baz"],
                                    ],
                                },
                                {
                                    "filename": "app/views.py",
                                    "lineNo": 42,
                                    "function": "home",
                                    "context": [
                                        [40, "def home(request):"],
                                        [41, "    obj = get_object()"],
                                        [42, "    result = obj.foo()"],
                                    ],
                                },
                            ]
                        },
                    }
                ]
            },
        },
        {
            "type": "breadcrumbs",
            "data": {
                "values": [
                    {
                        "category": "http",
                        "level": "info",
                        "message": "GET /api/data [200]",
                        "timestamp": "2026-02-17T14:29:58Z",
                    },
                    {
                        "category": "ui.click",
                        "level": "info",
                        "message": "button#submit",
                        "timestamp": "2026-02-17T14:29:59Z",
                    },
                ]
            },
        },
        {
            "type": "request",
            "data": {
                "url": "https://example.com/api/data",
                "method": "GET",
                "headers": [["Content-Type", "application/json"], ["Accept", "text/html"]],
            },
        },
    ],
    "contexts": {
        "browser": {"name": "Chrome", "version": "121.0"},
        "os": {"name": "Windows", "version": "10"},
    },
}


def test_fetch_latest_event_success():
    config = {"token": "test-token"}
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_EVENT_RESPONSE

    with patch.object(mod.requests, "get", return_value=mock_response) as mock_get:
        result = fetch_latest_event(config, "myorg", "12345")

    mock_get.assert_called_once_with(
        "https://sentry.io/api/0/organizations/myorg/issues/12345/events/latest/",
        headers={"Authorization": "Bearer test-token"},
        timeout=30,
    )
    assert result["eventID"] == "abc123"


def test_fetch_latest_event_none_on_404():
    config = {"token": "test-token"}
    mock_response = Mock()
    mock_response.status_code = 404

    with patch.object(mod.requests, "get", return_value=mock_response):
        result = fetch_latest_event(config, "myorg", "12345")

    assert result is None


def test_fetch_latest_event_connection_error():
    config = {"token": "test-token"}
    with patch.object(mod.requests, "get", side_effect=mod.requests.ConnectionError("refused")):
        with pytest.raises(SentryError, match="Could not connect"):
            fetch_latest_event(config, "myorg", "12345")


def test_fetch_latest_event_auth_failure():
    config = {"token": "test-token"}
    mock_response = Mock()
    mock_response.status_code = 401

    with patch.object(mod.requests, "get", return_value=mock_response):
        with pytest.raises(SentryError, match="Authentication failed"):
            fetch_latest_event(config, "myorg", "12345")


def test_fetch_latest_event_server_error():
    config = {"token": "test-token"}
    mock_response = Mock()
    mock_response.status_code = 500

    with patch.object(mod.requests, "get", return_value=mock_response):
        with pytest.raises(SentryError, match="500"):
            fetch_latest_event(config, "myorg", "12345")


SAMPLE_TAGS = [
    {"key": "environment", "values": [{"value": "production", "count": 30}, {"value": "staging", "count": 5}]},
    {"key": "browser", "values": [{"value": "Chrome 121.0", "count": 10}]},
]


def test_fetch_tag_details():
    config = {"token": "test-token"}

    def mock_get(url, headers=None, timeout=None):
        resp = Mock()
        resp.status_code = 200
        if "/tags/environment/" in url:
            resp.json.return_value = {
                "key": "environment",
                "topValues": [
                    {"value": "production", "count": 30},
                    {"value": "staging", "count": 5},
                ],
            }
        elif "/tags/browser/" in url:
            resp.json.return_value = {
                "key": "browser",
                "topValues": [{"value": "Chrome 121.0", "count": 10}],
            }
        return resp

    with patch.object(mod.requests, "get", side_effect=mock_get):
        result = fetch_tag_details(config, "myorg", "12345", ["environment", "browser"])

    assert len(result) == 2
    assert result[0]["key"] == "environment"
    assert len(result[0]["values"]) == 2
    assert result[0]["values"][0]["value"] == "production"
    assert result[1]["key"] == "browser"
    assert result[1]["values"][0]["value"] == "Chrome 121.0"


def test_fetch_tag_details_skips_failed():
    config = {"token": "test-token"}
    mock_response = Mock()
    mock_response.status_code = 404

    with patch.object(mod.requests, "get", return_value=mock_response):
        result = fetch_tag_details(config, "myorg", "12345", ["nonexistent"])

    assert result == []


def test_fetch_tag_details_connection_error():
    config = {"token": "test-token"}
    with patch.object(mod.requests, "get", side_effect=mod.requests.ConnectionError("refused")):
        result = fetch_tag_details(config, "myorg", "12345", ["environment"])
    assert result == []


SAMPLE_THREADS_EVENT = {
    "eventID": "thread123",
    "title": "Uncaught exit - {:timeout, {Task.Supervised, :stream, [15000]}}",
    "message": "Uncaught exit - {:timeout, {Task.Supervised, :stream, [15000]}}",
    "entries": [
        {
            "type": "message",
            "data": {
                "formatted": "Uncaught exit - {:timeout, {Task.Supervised, :stream, [15000]}}",
            },
        },
        {
            "type": "threads",
            "data": {
                "values": [
                    {
                        "id": "f09c45ff",
                        "name": None,
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "lib/plug/cowboy/handler.ex",
                                    "lineNo": 11,
                                    "function": "Plug.Cowboy.Handler.init/2",
                                    "context": [],
                                },
                                {
                                    "filename": "lib/care_bridge_web/controllers/dashboard_controller.ex",
                                    "lineNo": 80,
                                    "function": "CBWeb.DashboardController.overview_outstanding/2",
                                    "context": [],
                                },
                            ],
                        },
                    }
                ],
            },
        },
        {
            "type": "request",
            "data": {
                "url": "http://example.com/api/dashboard",
                "method": "GET",
                "headers": [],
            },
        },
    ],
    "contexts": {},
}


def test_extract_message_from_entry():
    result = extract_message(SAMPLE_THREADS_EVENT)
    assert result == "Uncaught exit - {:timeout, {Task.Supervised, :stream, [15000]}}"


def test_extract_message_fallback_to_top_level():
    result = extract_message(SAMPLE_EVENT_RESPONSE)
    assert result == "Cannot read property 'foo' of undefined"


def test_extract_message_no_message():
    assert extract_message({"entries": []}) == ""


def test_extract_threads():
    result = extract_threads(SAMPLE_THREADS_EVENT)
    assert len(result) == 1
    assert result[0]["id"] == "f09c45ff"
    assert len(result[0]["frames"]) == 2
    assert result[0]["frames"][0]["filename"] == "lib/plug/cowboy/handler.ex"
    assert result[0]["frames"][1]["function"] == "CBWeb.DashboardController.overview_outstanding/2"


def test_extract_threads_no_entries():
    assert extract_threads({"entries": []}) == []


def test_extract_exceptions():
    result = extract_exceptions(SAMPLE_EVENT_RESPONSE)
    assert len(result) == 1
    assert result[0]["type"] == "TypeError"
    assert result[0]["value"] == "Cannot read property 'foo' of undefined"
    assert len(result[0]["frames"]) == 2
    assert result[0]["frames"][0]["filename"] == "app/models.py"
    assert result[0]["frames"][0]["line"] == 18
    assert result[0]["frames"][0]["function"] == "foo"


def test_extract_exceptions_no_entries():
    assert extract_exceptions({"entries": []}) == []


def test_extract_breadcrumbs():
    result = extract_breadcrumbs(SAMPLE_EVENT_RESPONSE)
    assert len(result) == 2
    assert result[0]["category"] == "http"
    assert result[1]["category"] == "ui.click"


def test_extract_breadcrumbs_no_entries():
    assert extract_breadcrumbs({"entries": []}) == []


def test_extract_request():
    result = extract_request(SAMPLE_EVENT_RESPONSE)
    assert result["url"] == "https://example.com/api/data"
    assert result["method"] == "GET"


def test_extract_request_none():
    assert extract_request({"entries": []}) is None


def test_extract_contexts():
    result = extract_contexts(SAMPLE_EVENT_RESPONSE)
    assert result["browser"] == {"name": "Chrome", "version": "121.0"}
    assert result["os"] == {"name": "Windows", "version": "10"}


def test_extract_contexts_empty():
    assert extract_contexts({"contexts": {}}) == {}


def test_format_markdown():
    result = format_markdown(SAMPLE_ISSUE_RESPONSE, SAMPLE_EVENT_RESPONSE, SAMPLE_TAGS)
    assert "# TypeError: Cannot read property 'foo' of undefined" in result
    assert "| Level" in result
    assert "error" in result
    assert "unresolved" in result
    assert "42" in result
    assert "12" in result
    assert "## Tags" in result
    assert "environment" in result
    assert "production (30)" in result
    assert "staging (5)" in result
    assert "## Exception" in result
    assert "**TypeError**" in result
    assert "## Stack Trace" in result
    assert "app/views.py" in result
    assert "line 42" in result
    assert "home" in result
    assert "## Breadcrumbs" in result
    assert "http" in result
    assert "GET /api/data" in result
    assert "## Request" in result
    assert "https://example.com/api/data" in result
    assert "## Contexts" in result
    assert "Chrome" in result


def test_format_markdown_includes_message():
    event_with_message = {
        **SAMPLE_EVENT_RESPONSE,
        "message": "Error processing request for user 123: Cannot read property 'foo' of undefined",
    }
    result = format_markdown(SAMPLE_ISSUE_RESPONSE, event_with_message, SAMPLE_TAGS)
    assert "## Message" in result
    assert "Error processing request for user 123" in result


def test_format_markdown_skips_duplicate_message():
    """Message is skipped when it matches the issue title."""
    event_same_message = {
        **SAMPLE_EVENT_RESPONSE,
        "message": "TypeError: Cannot read property 'foo' of undefined",
    }
    result = format_markdown(SAMPLE_ISSUE_RESPONSE, event_same_message, SAMPLE_TAGS)
    assert "## Message" not in result


def test_format_markdown_threads_event():
    issue = {**SAMPLE_ISSUE_RESPONSE, "title": SAMPLE_THREADS_EVENT["title"]}
    result = format_markdown(issue, SAMPLE_THREADS_EVENT)
    assert "## Stack Trace" in result
    assert "dashboard_controller.ex" in result
    assert "line 80" in result
    assert "## Exception" not in result


def test_format_markdown_no_event():
    result = format_markdown(SAMPLE_ISSUE_RESPONSE, None)
    assert "# TypeError: Cannot read property 'foo' of undefined" in result
    assert "## Tags" not in result
    assert "No event data available" in result


def test_format_json():
    result = format_json(SAMPLE_ISSUE_RESPONSE, SAMPLE_EVENT_RESPONSE, SAMPLE_TAGS)
    parsed = json.loads(result)
    assert parsed["title"] == "TypeError: Cannot read property 'foo' of undefined"
    assert parsed["level"] == "error"
    assert parsed["status"] == "unresolved"
    assert parsed["count"] == "42"
    assert parsed["user_count"] == 12
    assert len(parsed["tags"]) == 2
    assert parsed["tags"][0]["key"] == "environment"
    assert len(parsed["tags"][0]["values"]) == 2
    assert parsed["tags"][0]["values"][0]["value"] == "production"
    assert len(parsed["exceptions"]) == 1
    assert parsed["exceptions"][0]["type"] == "TypeError"
    assert len(parsed["exceptions"][0]["frames"]) == 2
    assert len(parsed["breadcrumbs"]) == 2
    assert parsed["request"]["method"] == "GET"
    assert "browser" in parsed["contexts"]


def test_format_json_includes_message():
    result = format_json(SAMPLE_ISSUE_RESPONSE, SAMPLE_EVENT_RESPONSE, SAMPLE_TAGS)
    parsed = json.loads(result)
    assert parsed["message"] == "Cannot read property 'foo' of undefined"


def test_format_json_threads_event():
    issue = {**SAMPLE_ISSUE_RESPONSE, "title": SAMPLE_THREADS_EVENT["title"]}
    result = format_json(issue, SAMPLE_THREADS_EVENT)
    parsed = json.loads(result)
    assert parsed["exceptions"] == []
    assert len(parsed["threads"]) == 1
    assert len(parsed["threads"][0]["frames"]) == 2
    assert parsed["message"] == "Uncaught exit - {:timeout, {Task.Supervised, :stream, [15000]}}"


def test_format_json_no_event():
    result = format_json(SAMPLE_ISSUE_RESPONSE, None)
    parsed = json.loads(result)
    assert parsed["title"] == "TypeError: Cannot read property 'foo' of undefined"
    assert parsed["exceptions"] == []
    assert parsed["breadcrumbs"] == []
    assert parsed["request"] is None
    assert parsed["contexts"] == {}
    assert parsed["tags"] == []


def test_format_plain():
    result = format_plain(SAMPLE_ISSUE_RESPONSE, SAMPLE_EVENT_RESPONSE, SAMPLE_TAGS)
    assert "TypeError: Cannot read property 'foo' of undefined" in result
    assert "Level:" in result
    assert "error" in result
    assert "unresolved" in result
    assert "42" in result
    assert "# " not in result  # No markdown headers
    assert "| " not in result  # No markdown tables
    assert "Tags:" in result
    assert "environment:" in result
    assert "production (30)" in result
    assert "Exception: TypeError" in result
    assert "Stack Trace:" in result
    assert "app/views.py" in result
    assert "line 42" in result
    assert "Breadcrumbs:" in result
    assert "GET /api/data" in result
    assert "Request: GET" in result
    assert "Contexts:" in result
    assert "Chrome" in result


def test_format_plain_no_event():
    result = format_plain(SAMPLE_ISSUE_RESPONSE, None)
    assert "TypeError: Cannot read property 'foo' of undefined" in result
    assert "No event data available" in result


def test_format_plain_threads_event():
    issue = {**SAMPLE_ISSUE_RESPONSE, "title": SAMPLE_THREADS_EVENT["title"]}
    result = format_plain(issue, SAMPLE_THREADS_EVENT)
    assert "Stack Trace:" in result
    assert "dashboard_controller.ex" in result
    assert "Exception:" not in result


def test_included_tags_filters_keys():
    assert "environment" in INCLUDED_TAGS
    assert "runtime" in INCLUDED_TAGS
    assert "state" in INCLUDED_TAGS


def test_main_filters_tag_keys(monkeypatch, capsys):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")

    issue_with_many_tags = {
        **SAMPLE_ISSUE_RESPONSE,
        "tags": [
            {"key": "environment", "name": "Environment", "totalValues": 30},
            {"key": "browser", "name": "Browser", "totalValues": 10},
            {"key": "runtime", "name": "Runtime", "totalValues": 5},
        ],
    }

    with patch("truss.sentry_extractor.fetch_issue", return_value=issue_with_many_tags):
        with patch("truss.sentry_extractor.fetch_latest_event", return_value=SAMPLE_EVENT_RESPONSE):
            with patch("truss.sentry_extractor.fetch_tag_details", return_value=SAMPLE_TAGS) as mock_fetch_tags:
                with patch("sys.argv", ["sentry_extractor", "https://myorg.sentry.io/issues/12345/"]):
                    main()

    # Should only pass environment and runtime, not browser
    called_keys = mock_fetch_tags.call_args[0][3]
    assert "environment" in called_keys
    assert "runtime" in called_keys
    assert "browser" not in called_keys


def test_main_markdown_output(monkeypatch, capsys):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")

    with patch("truss.sentry_extractor.fetch_issue", return_value=SAMPLE_ISSUE_RESPONSE):
        with patch("truss.sentry_extractor.fetch_latest_event", return_value=SAMPLE_EVENT_RESPONSE):
            with patch("truss.sentry_extractor.fetch_tag_details", return_value=SAMPLE_TAGS):
                with patch("sys.argv", ["sentry_extractor", "https://myorg.sentry.io/issues/12345/"]):
                    main()

    captured = capsys.readouterr()
    assert "# TypeError: Cannot read property 'foo' of undefined" in captured.out


def test_main_json_output(monkeypatch, capsys):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")

    with patch("truss.sentry_extractor.fetch_issue", return_value=SAMPLE_ISSUE_RESPONSE):
        with patch("truss.sentry_extractor.fetch_latest_event", return_value=SAMPLE_EVENT_RESPONSE):
            with patch("truss.sentry_extractor.fetch_tag_details", return_value=SAMPLE_TAGS):
                with patch("sys.argv", ["sentry_extractor", "https://myorg.sentry.io/issues/12345/", "--format", "json"]):
                    main()

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["title"] == "TypeError: Cannot read property 'foo' of undefined"


def test_main_plain_output(monkeypatch, capsys):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")

    with patch("truss.sentry_extractor.fetch_issue", return_value=SAMPLE_ISSUE_RESPONSE):
        with patch("truss.sentry_extractor.fetch_latest_event", return_value=SAMPLE_EVENT_RESPONSE):
            with patch("truss.sentry_extractor.fetch_tag_details", return_value=SAMPLE_TAGS):
                with patch("sys.argv", ["sentry_extractor", "https://myorg.sentry.io/issues/12345/", "--format", "plain"]):
                    main()

    captured = capsys.readouterr()
    assert "TypeError: Cannot read property 'foo' of undefined" in captured.out
    assert "# " not in captured.out


# ---------------------------------------------------------------------------
# Thread context line rendering
# ---------------------------------------------------------------------------

SAMPLE_THREADS_WITH_CONTEXT_EVENT = {
    "eventID": "ctx123",
    "title": "Timeout error",
    "message": "Timeout error",
    "entries": [
        {
            "type": "threads",
            "data": {
                "values": [
                    {
                        "id": "thread1",
                        "name": None,
                        "stacktrace": {
                            "frames": [
                                {
                                    "filename": "lib/app/handler.ex",
                                    "lineNo": 42,
                                    "function": "MyApp.Handler.call/2",
                                    "context": [
                                        [40, "  def call(conn, opts) do"],
                                        [41, "    params = conn.params"],
                                        [42, "    process(params)"],
                                    ],
                                },
                            ],
                        },
                    }
                ],
            },
        },
    ],
    "contexts": {},
}


def test_format_markdown_thread_with_context():
    issue = {**SAMPLE_ISSUE_RESPONSE, "title": "Timeout error"}
    result = format_markdown(issue, SAMPLE_THREADS_WITH_CONTEXT_EVENT)
    assert "## Stack Trace" in result
    assert "process(params)" in result


def test_format_plain_thread_with_context():
    issue = {**SAMPLE_ISSUE_RESPONSE, "title": "Timeout error"}
    result = format_plain(issue, SAMPLE_THREADS_WITH_CONTEXT_EVENT)
    assert "Stack Trace:" in result
    assert "process(params)" in result


def test_main_sentry_error_exits(monkeypatch, capsys):
    monkeypatch.setenv("SENTRY_AUTH_TOKEN", "test-token")

    with patch("truss.sentry_extractor.fetch_issue", side_effect=SentryError("test error")):
        with patch("sys.argv", ["sentry_extractor", "https://myorg.sentry.io/issues/12345/"]):
            with pytest.raises(SystemExit):
                main()

    captured = capsys.readouterr()
    assert "test error" in captured.err
