"""Fetch Sentry issue details for AI-assisted debugging."""

import argparse
import json
import os
import re
import sys

import requests
from dotenv import load_dotenv


class SentryError(Exception):
    """Raised when a Sentry API operation fails."""


INCLUDED_TAGS = [
    "environment",
    "runtime",
    "state",
]

# Canonical Sentry URL patterns (with capture groups for org_slug and issue_id).
SENTRY_URL_PATTERNS = [
    r"https?://([^.]+)\.sentry\.io/issues/(\d+)",
    r"https?://([^.]+)\.sentry\.io/organizations/[^/]+/issues/(\d+)",
]

# Variants for finding Sentry URLs in free text (no capture groups, greedy suffix).
SENTRY_URL_SEARCH_PATTERNS = [
    r"https?://[^\s)>\"']+\.sentry\.io/issues/\d+[^\s)>\"']*",
    r"https?://[^\s)>\"']+\.sentry\.io/organizations/[^\s)>\"']+/issues/\d+[^\s)>\"']*",
]


def parse_issue_url(url):
    """Parse a Sentry issue URL and return (org_slug, issue_id)."""
    for pattern in SENTRY_URL_PATTERNS:
        match = re.match(pattern, url)
        if match:
            return match.group(1), match.group(2)
    raise ValueError(f"Could not parse Sentry issue URL: {url}")


def load_config():
    """Load Sentry auth token from environment."""
    token = os.environ.get("SENTRY_AUTH_TOKEN")
    if not token:
        raise SentryError("Missing SENTRY_AUTH_TOKEN environment variable.")
    return {"token": token}


def fetch_issue(config, org_slug, issue_id):
    """Fetch issue summary from Sentry API."""
    url = f"https://sentry.io/api/0/organizations/{org_slug}/issues/{issue_id}/"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {config['token']}"},
            timeout=30,
        )
    except requests.ConnectionError:
        raise SentryError("Could not connect to Sentry API.")

    if response.status_code == 404:
        raise SentryError(f"Issue {issue_id} not found.")
    elif response.status_code in (401, 403):
        raise SentryError("Authentication failed. Check your SENTRY_AUTH_TOKEN.")
    elif response.status_code != 200:
        raise SentryError(f"Sentry API returned status {response.status_code}.")

    return response.json()


def fetch_latest_event(config, org_slug, issue_id):
    """Fetch the latest event for an issue. Returns None if no events."""
    url = f"https://sentry.io/api/0/organizations/{org_slug}/issues/{issue_id}/events/latest/"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {config['token']}"},
            timeout=30,
        )
    except requests.ConnectionError:
        raise SentryError("Could not connect to Sentry API.")

    if response.status_code == 404:
        return None
    elif response.status_code in (401, 403):
        raise SentryError("Authentication failed. Check your SENTRY_AUTH_TOKEN.")
    elif response.status_code != 200:
        raise SentryError(f"Sentry API returned status {response.status_code}.")

    return response.json()


def _extract_frames(stacktrace):
    """Extract frame data from a stacktrace dict."""
    frames = []
    for frame in stacktrace.get("frames", []):
        frames.append({
            "filename": frame.get("filename", "?"),
            "line": frame.get("lineNo"),
            "function": frame.get("function", "?"),
            "context": frame.get("context", []),
        })
    return frames


def _render_frames(frames):
    """Render stack trace frames as indented text lines (most recent call first)."""
    lines = []
    for frame in reversed(frames):
        line_info = f", line {frame['line']}" if frame["line"] else ""
        lines.append(f"  File \"{frame['filename']}\"{line_info}, in {frame['function']}")
        for ctx in frame.get("context", []):
            if len(ctx) == 2 and ctx[0] == frame["line"]:
                lines.append(f"    {ctx[1].strip()}")
    return lines


def extract_message(event):
    """Extract message from event entries (type=message with data.formatted) or top-level."""
    for entry in event.get("entries", []):
        if entry.get("type") == "message":
            formatted = entry.get("data", {}).get("formatted", "")
            if formatted:
                return formatted
    return event.get("message", "")


def extract_exceptions(event):
    """Extract exception data from event entries (type=exception)."""
    for entry in event.get("entries", []):
        if entry.get("type") == "exception":
            exceptions = []
            for exc in entry["data"].get("values", []):
                frames = _extract_frames(exc.get("stacktrace") or {})
                exceptions.append({
                    "type": exc.get("type", "Unknown"),
                    "value": exc.get("value", ""),
                    "frames": frames,
                })
            return exceptions
    return []


def extract_threads(event):
    """Extract stacktraces from thread entries (type=threads)."""
    for entry in event.get("entries", []):
        if entry.get("type") == "threads":
            threads = []
            for thread in entry["data"].get("values", []):
                stacktrace = thread.get("stacktrace")
                if stacktrace:
                    frames = _extract_frames(stacktrace)
                    if frames:
                        threads.append({
                            "id": thread.get("id"),
                            "name": thread.get("name"),
                            "frames": frames,
                        })
            return threads
    return []


def extract_breadcrumbs(event):
    """Extract breadcrumbs from event entries."""
    for entry in event.get("entries", []):
        if entry.get("type") == "breadcrumbs":
            return [
                {
                    "category": b.get("category", ""),
                    "level": b.get("level", ""),
                    "message": b.get("message", ""),
                    "timestamp": b.get("timestamp", ""),
                }
                for b in entry["data"].get("values", [])
            ]
    return []


def extract_request(event):
    """Extract request data from event entries."""
    for entry in event.get("entries", []):
        if entry.get("type") == "request":
            data = entry["data"]
            return {
                "url": data.get("url", ""),
                "method": data.get("method", ""),
                "headers": data.get("headers", []),
            }
    return None


def extract_contexts(event):
    """Extract context data from event."""
    return event.get("contexts", {})


def fetch_tag_details(config, org_slug, issue_id, tag_keys):
    """Fetch detailed tag values for each tag key. Returns list of {key, values: [{value, count}]}."""
    headers = {"Authorization": f"Bearer {config['token']}"}
    tags = []
    for key in tag_keys:
        url = f"https://sentry.io/api/0/organizations/{org_slug}/issues/{issue_id}/tags/{key}/"
        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.ConnectionError:
            continue
        if response.status_code != 200:
            continue
        data = response.json()
        top_values = [
            {"value": v.get("value", ""), "count": v.get("count", 0)}
            for v in data.get("topValues", [])
        ]
        if top_values:
            tags.append({"key": key, "values": top_values})
    return tags


def format_markdown(issue, event, tags=None):
    """Format issue + event as markdown for AI consumption."""
    title = issue["title"]
    level = issue.get("level", "unknown")
    status = issue.get("status", "unknown")
    count = issue.get("count", "0")
    user_count = issue.get("userCount", 0)
    first_seen = issue.get("firstSeen", "unknown")
    last_seen = issue.get("lastSeen", "unknown")
    culprit = issue.get("culprit", "unknown")
    permalink = issue.get("permalink", "")

    lines = []
    lines.append(f"# {title}\n")
    lines.append("| Field      | Value |")
    lines.append("|------------|-------|")
    lines.append(f"| Level      | {level} |")
    lines.append(f"| Status     | {status} |")
    lines.append(f"| Events     | {count} |")
    lines.append(f"| Users      | {user_count} |")
    lines.append(f"| First Seen | {first_seen} |")
    lines.append(f"| Last Seen  | {last_seen} |")
    lines.append(f"| Culprit    | {culprit} |")
    lines.append(f"| Link       | {permalink} |")

    if tags:
        lines.append("\n## Tags\n")
        lines.append("| Key | Values |")
        lines.append("|-----|--------|")
        for tag in tags:
            values_str = ", ".join(
                f"{v['value']} ({v['count']})" for v in tag["values"]
            )
            lines.append(f"| {tag['key']} | {values_str} |")

    if event is None:
        lines.append("\nNo event data available.\n")
        return "\n".join(lines)

    message = extract_message(event)
    if message and message != title:
        lines.append(f"\n## Message\n")
        lines.append(f"```\n{message}\n```")

    exceptions = extract_exceptions(event)
    for exc in exceptions:
        lines.append(f"\n## Exception\n")
        lines.append(f"**{exc['type']}**: {exc['value']}\n")
        if exc["frames"]:
            lines.append("## Stack Trace\n")
            lines.append("```")
            lines.extend(_render_frames(exc["frames"]))
            lines.append("```")

    if not exceptions:
        threads = extract_threads(event)
        for thread in threads:
            lines.append(f"\n## Stack Trace\n")
            lines.append("```")
            lines.extend(_render_frames(thread["frames"]))
            lines.append("```")

    breadcrumbs = extract_breadcrumbs(event)
    if breadcrumbs:
        lines.append("\n## Breadcrumbs\n")
        lines.append("| Time | Category | Level | Message |")
        lines.append("|------|----------|-------|---------|")
        for b in breadcrumbs[-20:]:
            ts = b["timestamp"].split("T")[-1].rstrip("Z") if "T" in b["timestamp"] else b["timestamp"]
            lines.append(f"| {ts} | {b['category']} | {b['level']} | {b['message']} |")

    req = extract_request(event)
    if req:
        lines.append("\n## Request\n")
        lines.append(f"**{req['method']}** `{req['url']}`\n")
        if req["headers"]:
            lines.append("**Headers:**\n")
            for header in req["headers"]:
                if len(header) == 2:
                    lines.append(f"- `{header[0]}`: `{header[1]}`")

    contexts = extract_contexts(event)
    if contexts:
        lines.append("\n## Contexts\n")
        lines.append("| Context | Details |")
        lines.append("|---------|---------|")
        for ctx_name, ctx_data in contexts.items():
            if isinstance(ctx_data, dict):
                details = ", ".join(f"{k}: {v}" for k, v in ctx_data.items() if k != "type")
                lines.append(f"| {ctx_name} | {details} |")

    return "\n".join(lines)


def format_json(issue, event, tags=None):
    """Format issue + event as JSON for programmatic consumption."""
    data = {
        "title": issue["title"],
        "level": issue.get("level", "unknown"),
        "status": issue.get("status", "unknown"),
        "count": issue.get("count", "0"),
        "user_count": issue.get("userCount", 0),
        "first_seen": issue.get("firstSeen"),
        "last_seen": issue.get("lastSeen"),
        "culprit": issue.get("culprit"),
        "permalink": issue.get("permalink"),
        "message": None,
        "tags": tags or [],
        "exceptions": [],
        "threads": [],
        "breadcrumbs": [],
        "request": None,
        "contexts": {},
    }

    if event:
        data["message"] = extract_message(event)
        data["exceptions"] = extract_exceptions(event)
        data["threads"] = extract_threads(event)
        data["breadcrumbs"] = extract_breadcrumbs(event)
        data["request"] = extract_request(event)
        data["contexts"] = extract_contexts(event)

    return json.dumps(data, indent=2)


def format_plain(issue, event, tags=None):
    """Format issue + event as plain text for human consumption."""
    title = issue["title"]
    level = issue.get("level", "unknown")
    status = issue.get("status", "unknown")
    count = issue.get("count", "0")
    user_count = issue.get("userCount", 0)
    first_seen = issue.get("firstSeen", "unknown")
    last_seen = issue.get("lastSeen", "unknown")
    culprit = issue.get("culprit", "unknown")
    permalink = issue.get("permalink", "")

    lines = []
    lines.append(title)
    lines.append("")
    lines.append(f"Level:      {level}")
    lines.append(f"Status:     {status}")
    lines.append(f"Events:     {count}")
    lines.append(f"Users:      {user_count}")
    lines.append(f"First Seen: {first_seen}")
    lines.append(f"Last Seen:  {last_seen}")
    lines.append(f"Culprit:    {culprit}")
    lines.append(f"Link:       {permalink}")

    if tags:
        lines.append("")
        lines.append("Tags:")
        for tag in tags:
            values_str = ", ".join(
                f"{v['value']} ({v['count']})" for v in tag["values"]
            )
            lines.append(f"  {tag['key']}: {values_str}")

    if event is None:
        lines.append("")
        lines.append("No event data available.")
        return "\n".join(lines)

    message = extract_message(event)
    if message and message != title:
        lines.append("")
        lines.append("Message:")
        lines.append(f"  {message}")

    exceptions = extract_exceptions(event)
    for exc in exceptions:
        lines.append("")
        lines.append(f"Exception: {exc['type']}: {exc['value']}")
        if exc["frames"]:
            lines.append("")
            lines.append("Stack Trace:")
            lines.extend(_render_frames(exc["frames"]))

    if not exceptions:
        threads = extract_threads(event)
        for thread in threads:
            lines.append("")
            lines.append("Stack Trace:")
            lines.extend(_render_frames(thread["frames"]))

    breadcrumbs = extract_breadcrumbs(event)
    if breadcrumbs:
        lines.append("")
        lines.append("Breadcrumbs:")
        for b in breadcrumbs[-20:]:
            ts = b["timestamp"].split("T")[-1].rstrip("Z") if "T" in b["timestamp"] else b["timestamp"]
            lines.append(f"  [{ts}] {b['category']} ({b['level']}): {b['message']}")

    req = extract_request(event)
    if req:
        lines.append("")
        lines.append(f"Request: {req['method']} {req['url']}")
        if req["headers"]:
            lines.append("Headers:")
            for header in req["headers"]:
                if len(header) == 2:
                    lines.append(f"  {header[0]}: {header[1]}")

    contexts = extract_contexts(event)
    if contexts:
        lines.append("")
        lines.append("Contexts:")
        for ctx_name, ctx_data in contexts.items():
            if isinstance(ctx_data, dict):
                details = ", ".join(f"{k}: {v}" for k, v in ctx_data.items() if k != "type")
                lines.append(f"  {ctx_name}: {details}")

    return "\n".join(lines)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Fetch Sentry issue details for AI-assisted debugging."
    )
    parser.add_argument("url", help="Sentry issue URL (e.g. https://myorg.sentry.io/issues/12345/)")
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "plain"],
        default="markdown",
        dest="output_format",
        help="Output format (default: markdown)",
    )
    args = parser.parse_args()

    try:
        org_slug, issue_id = parse_issue_url(args.url)
        config = load_config()
        issue = fetch_issue(config, org_slug, issue_id)
        event = fetch_latest_event(config, org_slug, issue_id)
        tag_keys = [t["key"] for t in issue.get("tags", []) if t["key"] in INCLUDED_TAGS]
        tags = fetch_tag_details(config, org_slug, issue_id, tag_keys)

        if args.output_format == "json":
            print(format_json(issue, event, tags))
        elif args.output_format == "plain":
            print(format_plain(issue, event, tags))
        else:
            print(format_markdown(issue, event, tags))
    except SentryError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
