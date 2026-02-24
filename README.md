# Truss

A truss isn't a single beam; it's a collection of many smaller members -- chords, struts, and ties -- that work together to create a structure stronger than the sum of its parts.

Truss is a collection of small, focused CLI tools that extract and format issue data from external services for AI-assisted workflows. Each extractor pulls structured context from a different source, and a unified entrypoint ties them together.

## Tools

| Command | Source | What it does |
|---------|--------|--------------|
| `extract-issue` | Auto-detected | Unified CLI -- detects whether the input is a Sentry URL, Jira URL, or Jira ticket key and routes to the right extractor |
| `jira_extractor` | Jira Cloud API v3 | Fetches ticket details (summary, description, acceptance criteria, status, assignee, custom fields) |
| `sentry_extractor` | Sentry API | Fetches issue details, latest event, stacktrace, and tag breakdowns |

All tools output in **markdown** (default), **plain text**, or **JSON**.

## Quick start

```bash
# Clone and install
git clone <repo-url> && cd truss
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure credentials (see Environment below)
cp .env.example .env   # then fill in values

# Extract an issue
extract-issue PROJ-123
extract-issue https://myorg.atlassian.net/browse/PROJ-123
extract-issue https://myorg.sentry.io/issues/456789 --format json
```

## Environment

Create a `.env` file (or export directly) with the variables each extractor needs:

**Jira:**
```
JIRA_URL=https://yourorg.atlassian.net
JIRA_EMAIL=you@yourorg.com
JIRA_API_TOKEN=your-api-token
```

**Sentry:**
```
SENTRY_AUTH_TOKEN=your-sentry-token
```

## Running tests

```bash
pytest
```

## Project structure

```
src/truss/
  extract_issue.py      # Unified CLI entrypoint
  jira_extractor.py     # Jira Cloud extractor
  sentry_extractor.py   # Sentry extractor
tests/
  test_jira_extractor.py
```

## License

Private.
