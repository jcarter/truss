"""Unified CLI for extracting issue details from Sentry or Jira."""

import argparse
import enum
import re
import sys

from dotenv import load_dotenv

from truss.sentry_extractor import (
    SentryError,
    SENTRY_URL_PATTERNS,
    parse_issue_url,
    load_config as sentry_load_config,
    fetch_issue as sentry_fetch_issue,
    fetch_latest_event,
    fetch_tag_details,
    format_markdown as sentry_format_markdown,
    format_json as sentry_format_json,
    format_plain as sentry_format_plain,
    INCLUDED_TAGS,
)
from truss.jira_extractor import (
    JiraError,
    JIRA_URL_PATTERN,
    JIRA_TICKET_KEY_PATTERN,
    parse_ticket_input,
    load_config as jira_load_config,
    fetch_ticket,
    format_markdown as jira_format_markdown,
    format_json as jira_format_json,
    format_plain as jira_format_plain,
)


class Source(enum.Enum):
    SENTRY = "sentry"
    JIRA = "jira"
    UNKNOWN = "unknown"


def detect_source(value):
    """Detect whether the input is a Sentry URL, Jira URL, or Jira ticket key."""
    for pattern in SENTRY_URL_PATTERNS:
        if re.match(pattern, value):
            return Source.SENTRY

    if re.match(JIRA_URL_PATTERN, value):
        return Source.JIRA

    if re.match(JIRA_TICKET_KEY_PATTERN, value):
        return Source.JIRA

    return Source.UNKNOWN


def run_sentry(value, output_format):
    """Run the sentry extractor and return formatted output."""
    org_slug, issue_id = parse_issue_url(value)
    config = sentry_load_config()
    issue = sentry_fetch_issue(config, org_slug, issue_id)
    event = fetch_latest_event(config, org_slug, issue_id)
    tag_keys = [t["key"] for t in issue.get("tags", []) if t["key"] in INCLUDED_TAGS]
    tags = fetch_tag_details(config, org_slug, issue_id, tag_keys)

    if output_format == "json":
        return sentry_format_json(issue, event, tags)
    elif output_format == "plain":
        return sentry_format_plain(issue, event, tags)
    else:
        return sentry_format_markdown(issue, event, tags)


def run_jira(value, output_format):
    """Run the jira extractor and return formatted output."""
    ticket_key = parse_ticket_input(value)
    config = jira_load_config()
    issue = fetch_ticket(config, ticket_key)

    if output_format == "json":
        return jira_format_json(issue)
    elif output_format == "plain":
        return jira_format_plain(issue)
    else:
        return jira_format_markdown(issue)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Extract issue details from Sentry or Jira."
    )
    parser.add_argument(
        "input",
        help=(
            "Sentry issue URL (https://org.sentry.io/issues/123), "
            "Jira URL (https://org.atlassian.net/browse/PROJ-123), "
            "or Jira ticket key (PROJ-123)"
        ),
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "plain"],
        default="markdown",
        dest="output_format",
        help="Output format (default: markdown)",
    )
    args = parser.parse_args()

    source = detect_source(args.input)

    try:
        if source == Source.SENTRY:
            output = run_sentry(args.input, args.output_format)
        elif source == Source.JIRA:
            output = run_jira(args.input, args.output_format)
        else:
            print(
                f"Error: Could not determine source for input: {args.input}\n"
                "Supported formats:\n"
                "  Sentry URL:  https://myorg.sentry.io/issues/12345/\n"
                "  Jira URL:    https://myorg.atlassian.net/browse/PROJ-123\n"
                "  Jira ticket: PROJ-123",
                file=sys.stderr,
            )
            sys.exit(1)
        print(output)
    except (SentryError, JiraError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
