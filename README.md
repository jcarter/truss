# Truss

A truss isn't a single beam; it's a collection of many smaller members -- chords, struts, and ties -- that work together to create a structure stronger than the sum of its parts.

Truss is a collection of small, focused CLI tools that extract and format issue data from external services for AI-assisted workflows. Each extractor pulls structured context from a different source, and a unified entrypoint ties them together.

## Tools

| Command | Source | What it does |
|---------|--------|--------------|
| `extract-issue` | Auto-detected | Unified CLI -- detects whether the input is a Sentry URL, Jira URL, or Jira ticket key and routes to the right extractor |
| `review-pr` | GitHub PR | Generates a code-review prompt from a PR -- checks out the branch, gathers linked Jira/Sentry context, and outputs a structured prompt for AI review |
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
```

## Usage

The editable install (`pip install -e .`) registers `extract-issue` as a console script inside the venv. As long as the venv is active you can call it directly:

```bash
extract-issue PROJ-123
extract-issue https://myorg.atlassian.net/browse/PROJ-123
extract-issue https://myorg.sentry.io/issues/456789 --format json
```

### PR review

```bash
# Generate a review prompt (checks out the PR branch, gathers context)
review-pr https://github.com/owner/repo/pull/123

# Pipe directly to Claude Code for automated review
review-pr https://github.com/owner/repo/pull/123 | claude
```

The tool auto-detects Jira ticket keys and Sentry URLs from the PR description and branch name, then includes that context in the prompt. Jira/Sentry credentials are optional -- if not configured, the review runs with just the diff.

Requires the [GitHub CLI](https://cli.github.com/) (`gh`) to be installed and authenticated.

If the venv is **not** active, use the full path to the venv's binary:

```bash
.venv/bin/extract-issue PROJ-123
```

or make an alias
```
alias extract-issue=".venv/bin/extract-issue"
```

You can also run the individual extractors as Python modules without needing the console script:

```bash
# With venv active
python -m truss.jira_extractor PROJ-123
python -m truss.sentry_extractor https://myorg.sentry.io/issues/456789

# Without venv active
.venv/bin/python -m truss.jira_extractor PROJ-123
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
# With venv active
pytest

# Without venv active
.venv/bin/pytest
```

## Project structure

```
src/truss/
  extract_issue.py      # Unified CLI entrypoint
  review_pr.py          # PR review prompt generator
  jira_extractor.py     # Jira Cloud extractor
  sentry_extractor.py   # Sentry extractor
tests/
  test_extract_issue.py
  test_review_pr.py
  test_jira_extractor.py
  test_sentry_extractor.py
```

## License

MIT
