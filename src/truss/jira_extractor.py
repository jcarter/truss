import argparse
import json
import os
import re
import sys

import requests
from atlas_doc_parser.api import NodeDoc
from dotenv import load_dotenv


class JiraError(Exception):
    """Raised when a Jira API operation fails."""


def parse_ticket_input(value):
    """Accept a Jira ticket key (PROJ-123) or a Jira URL and return the ticket key.

    Supported URL patterns:
        https://myorg.atlassian.net/browse/PROJ-123
        https://myorg.atlassian.net/browse/PROJ-123?extra=params
    """
    url_match = re.match(
        r"https?://[^/]+/browse/([A-Z][A-Z0-9_]+-\d+)",
        value,
    )
    if url_match:
        return url_match.group(1)

    if re.match(r"^[A-Z][A-Z0-9_]+-\d+$", value):
        return value

    raise ValueError(
        f"Could not parse Jira input: {value}\n"
        "Expected a ticket key (e.g. PROJ-123) or URL (e.g. https://myorg.atlassian.net/browse/PROJ-123)"
    )


def load_config():
    required = {
        "JIRA_URL": "url",
        "JIRA_EMAIL": "email",
        "JIRA_API_TOKEN": "token",
    }
    config = {}
    missing = []
    for env_var, key in required.items():
        value = os.environ.get(env_var)
        if not value:
            missing.append(env_var)
        else:
            config[key] = value

    if missing:
        raise JiraError(f"Missing required environment variables: {', '.join(missing)}")

    config["url"] = config["url"].rstrip("/")
    return config


FIELDS = "summary,description,status,issuetype,priority,assignee,reporter,customfield_11271"


def fetch_ticket(config, ticket_key):
    url = f"{config['url']}/rest/api/3/issue/{ticket_key}"

    try:
        response = requests.get(
            url,
            params={"fields": FIELDS},
            auth=(config["email"], config["token"]),
            timeout=30,
        )
    except requests.ConnectionError:
        raise JiraError(f"Could not connect to {config['url']}")

    if response.status_code == 404:
        raise JiraError(f"Ticket {ticket_key} not found.")
    elif response.status_code in (401, 403):
        raise JiraError("Authentication failed. Check your JIRA_EMAIL and JIRA_API_TOKEN.")
    elif response.status_code != 200:
        raise JiraError(f"Jira API returned status {response.status_code}.")

    return response.json()


def _clean_markdown(md):
    # Remove spurious <br> tags the library inserts in table cells
    md = re.sub(r"<br>", "", md)

    # Insert missing separator row after the first row of headerless tables
    lines = md.split("\n")
    result = []
    i = 0
    while i < len(lines):
        result.append(lines[i])
        if (
            lines[i].startswith("|")
            and lines[i].endswith("|")
            and (i + 1 >= len(lines) or not lines[i + 1].startswith("| ---"))
            and (i == 0 or not lines[i - 1].startswith("|"))
        ):
            # First row of a table with no separator — add one
            col_count = lines[i].count("|") - 1
            result.append("| " + " | ".join(["---"] * col_count) + " |")
        i += 1
    return "\n".join(result)


def adf_to_markdown(adf):
    if adf is None:
        return "No description provided."
    try:
        doc = NodeDoc.from_dict(adf)
        md = doc.to_markdown().strip()
        md = _clean_markdown(md)
        return md if md else "No description provided."
    except Exception:
        return extract_text_from_adf(adf)


def extract_text_from_adf(adf):
    if adf is None:
        return "No description provided."

    texts = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            for child in node.get("content", []):
                walk(child)

    walk(adf)
    return "\n\n".join(texts) if texts else "No description provided."


def _extract_custom_field(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("value") or value.get("name") or str(value)
    if isinstance(value, list):
        return ", ".join(_extract_custom_field(v) or "" for v in value)
    return str(value)


def format_markdown(issue):
    fields = issue["fields"]
    key = issue["key"]
    summary = fields["summary"]
    status = fields["status"]["name"]
    issue_type = fields["issuetype"]["name"]
    priority = fields["priority"]["name"] if fields.get("priority") else "None"
    assignee = fields["assignee"]["displayName"] if fields.get("assignee") else "Unassigned"
    reporter = fields["reporter"]["displayName"] if fields.get("reporter") else "Unknown"
    code_config = _extract_custom_field(fields.get("customfield_11271"))
    description = adf_to_markdown(fields.get("description"))

    return f"""# {key}: {summary}

| Field       | Value            |
|-------------|------------------|
| Status      | {status} |
| Type        | {issue_type} |
| Priority    | {priority} |
| Assignee    | {assignee} |
| Reporter    | {reporter} |
| Code/Config | {code_config or 'TBD'} |

## Description

{description}
"""


def format_plain(issue):
    fields = issue["fields"]
    key = issue["key"]
    summary = fields["summary"]
    status = fields["status"]["name"]
    issue_type = fields["issuetype"]["name"]
    priority = fields["priority"]["name"] if fields.get("priority") else "None"
    assignee = fields["assignee"]["displayName"] if fields.get("assignee") else "Unassigned"
    reporter = fields["reporter"]["displayName"] if fields.get("reporter") else "Unknown"
    code_config = _extract_custom_field(fields.get("customfield_11271"))
    description = extract_text_from_adf(fields.get("description"))

    return f"""{key}: {summary}

Status:      {status}
Type:        {issue_type}
Priority:    {priority}
Assignee:    {assignee}
Reporter:    {reporter}
Code/Config: {code_config or 'TBD'}

Description:

{description}
"""


def format_json(issue):
    fields = issue["fields"]
    return json.dumps(
        {
            "key": issue["key"],
            "summary": fields["summary"],
            "status": fields["status"]["name"],
            "type": fields["issuetype"]["name"],
            "priority": fields["priority"]["name"] if fields.get("priority") else None,
            "assignee": fields["assignee"]["displayName"] if fields.get("assignee") else None,
            "reporter": fields["reporter"]["displayName"] if fields.get("reporter") else None,
            "code_config": _extract_custom_field(fields.get("customfield_11271")),
            "description": adf_to_markdown(fields.get("description")),
        },
        indent=2,
    )


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Fetch Jira ticket details for AI consumption."
    )
    parser.add_argument(
        "ticket",
        help="Jira ticket key (e.g. PROJ-123) or URL (e.g. https://myorg.atlassian.net/browse/PROJ-123)",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "plain"],
        default="markdown",
        dest="output_format",
        help="Output format (default: markdown)",
    )
    args = parser.parse_args()

    try:
        ticket_key = parse_ticket_input(args.ticket)
        config = load_config()
        issue = fetch_ticket(config, ticket_key)

        if args.output_format == "json":
            print(format_json(issue))
        elif args.output_format == "plain":
            print(format_plain(issue))
        else:
            print(format_markdown(issue))
    except (ValueError, JiraError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
