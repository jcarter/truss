"""Generate a structured code-review prompt for a GitHub pull request."""

import argparse
import json
import re
import subprocess
import sys

from dotenv import load_dotenv

from truss.extract_issue import run_jira, run_sentry
from truss.jira_extractor import JiraError
from truss.sentry_extractor import SentryError, SENTRY_URL_SEARCH_PATTERNS


class ReviewPRError(Exception):
    """Raised when a PR review operation fails."""


def parse_pr_url(url):
    """Parse a GitHub PR URL and return (owner, repo, number).

    Supported patterns:
        https://github.com/owner/repo/pull/123
        https://github.com/owner/repo/pull/123/files
    """
    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)",
        url,
    )
    if not match:
        raise ValueError(
            f"Could not parse GitHub PR URL: {url}\n"
            "Expected: https://github.com/owner/repo/pull/123"
        )
    return match.group(1), match.group(2), match.group(3)


def run_gh(args):
    """Run a gh CLI command and return stdout.

    Raises ReviewPRError if the command fails.
    """
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise ReviewPRError(
            "The GitHub CLI (gh) is not installed.\n"
            "Install it: https://cli.github.com/"
        )
    except subprocess.TimeoutExpired:
        raise ReviewPRError("gh command timed out after 60 seconds.")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise ReviewPRError(f"gh command failed: {stderr}")

    return result.stdout


def fetch_pr_metadata(pr_url):
    """Fetch PR metadata using gh CLI. Returns a dict."""
    fields = "title,body,headRefName,baseRefName,author,number,url"
    raw = run_gh(["pr", "view", pr_url, "--json", fields])
    return json.loads(raw)


def fetch_pr_diff(pr_url):
    """Fetch the PR diff using gh CLI. Returns the diff string."""
    return run_gh(["pr", "diff", pr_url])


def validate_git_repo(owner, repo):
    """Verify that the current directory is a git repo matching the PR's repository.

    Checks that .git/config contains a remote URL matching owner/repo.
    Raises ReviewPRError if not in a matching git repo.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise ReviewPRError("git is not installed.")

    if result.returncode != 0:
        raise ReviewPRError(
            "Not in a git repository, or no 'origin' remote configured.\n"
            f"Please cd into a local clone of {owner}/{repo} before running this command."
        )

    remote_url = result.stdout.strip()
    # Match both HTTPS and SSH remote URL formats
    expected_patterns = [
        f"github.com/{owner}/{repo}",
        f"github.com:{owner}/{repo}",
    ]
    # Normalize: strip trailing .git for comparison
    normalized = remote_url.rstrip("/").removesuffix(".git")
    if not any(pattern in normalized for pattern in expected_patterns):
        raise ReviewPRError(
            f"Current git repo remote does not match the PR repository.\n"
            f"  Remote: {remote_url}\n"
            f"  Expected: {owner}/{repo}\n"
            f"Please cd into a local clone of {owner}/{repo}."
        )


def checkout_pr(pr_url):
    """Check out the PR branch locally using gh CLI."""
    run_gh(["pr", "checkout", pr_url])


def find_jira_keys(text):
    """Find Jira ticket keys in text (e.g. PROJ-123, MY_PROJ-99).

    Returns a deduplicated list preserving first-occurrence order.
    """
    if not text:
        return []
    matches = re.findall(r"\b([A-Z][A-Z0-9_]+-\d+)\b", text)
    seen = set()
    result = []
    for key in matches:
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def find_sentry_urls(text):
    """Find Sentry issue URLs in text.

    Returns a deduplicated list preserving first-occurrence order.
    """
    if not text:
        return []
    seen = set()
    result = []
    for pattern in SENTRY_URL_SEARCH_PATTERNS:
        for match in re.findall(pattern, text):
            # Normalize by stripping trailing slashes, query params, fragments for dedup
            normalized = re.sub(r"[?#].*$", "", match).rstrip("/")
            if normalized not in seen:
                seen.add(normalized)
                result.append(match)
    return result


def gather_context(jira_keys, sentry_urls):
    """Gather context from Jira and Sentry extractors.

    Gracefully skips if credentials are not configured.
    Returns a dict with 'jira' and 'sentry' lists of (key/url, output) tuples.
    """
    context = {"jira": [], "sentry": []}

    for key in jira_keys:
        try:
            output = run_jira(key, "markdown")
            context["jira"].append((key, output))
        except JiraError as e:
            print(f"Warning: Could not fetch Jira {key}: {e}", file=sys.stderr)

    for url in sentry_urls:
        try:
            output = run_sentry(url, "markdown")
            context["sentry"].append((url, output))
        except (SentryError, ValueError) as e:
            print(f"Warning: Could not fetch Sentry issue: {e}", file=sys.stderr)

    return context


def build_prompt(metadata, context):
    """Assemble the full review prompt from PR metadata and issue context."""
    author = metadata.get("author", {}).get("login", "unknown")
    title = metadata.get("title", "Untitled")
    number = metadata.get("number", "?")
    url = metadata.get("url", "")
    head = metadata.get("headRefName", "unknown")
    base = metadata.get("baseRefName", "unknown")
    body = metadata.get("body", "") or ""

    sections = []

    # Header
    sections.append(f"# Code Review: PR #{number} — {title}\n")
    sections.append(
        f"| Field  | Value |\n"
        f"|--------|-------|\n"
        f"| Author | {author} |\n"
        f"| Branch | {head} → {base} |\n"
        f"| URL    | {url} |\n"
    )

    # PR description
    if body.strip():
        sections.append(f"## PR Description\n\n{body.strip()}\n")

    # Linked issue context
    if context["jira"] or context["sentry"]:
        sections.append("## Linked Issue Context\n")
        for key, output in context["jira"]:
            sections.append(f"### Jira: {key}\n\n{output}\n")
        for sentry_url, output in context["sentry"]:
            sections.append(f"### Sentry Issue\n\n{output}\n")

    # Parse owner/repo from URL for gh commands
    owner_repo = ""
    if url:
        match = re.match(r"https?://github\.com/([^/]+/[^/]+)", url)
        if match:
            owner_repo = match.group(1)

    # Review instructions
    sections.append(
        "## Review Instructions\n\n"
        "The PR branch is already checked out locally. Use local git to review the changes:\n"
        "```\n"
        f"git log {base}..HEAD --oneline\n"
        "```\n"
        "Then review the full diff against the base branch:\n"
        "```\n"
        f"git diff {base}..HEAD\n"
        "```\n"
        "Or review individual commits for more context:\n"
        "```\n"
        "git show <commit>\n"
        "```\n\n"
        "Review this pull request. For each issue found, provide:\n"
        "- **File path** and **line number(s)** in the diff\n"
        "- **Severity**: critical / warning / suggestion\n"
        "- **Description** of the issue\n"
        "- **Suggested fix** (if applicable)\n\n"
        "Focus on: bugs, security vulnerabilities, logic errors, missing error handling, "
        "performance issues, and whether the changes address the linked issue requirements.\n"
    )

    # Posting comments with gh-pr-review
    sections.append(
        "## Posting Review Comments\n\n"
        "Use the `gh pr-review` CLI extension to post inline review comments.\n"
        "Install it if not already available: `gh extension install agynio/gh-pr-review`\n\n"
        "### Workflow\n\n"
        "1. **Start a pending review:**\n"
        "```\n"
        f"gh pr-review review start -R {owner_repo} {number}\n"
        "```\n\n"
        "2. **Add inline comments** for each finding (repeat for each issue):\n"
        "```\n"
        f"gh pr-review review add-comment -R {owner_repo} {number} \\\n"
        "  --path <file> --line <line> --body '<comment>'\n"
        "```\n"
        "For multi-line comments, use `--start-line <start> --line <end>`.\n\n"
        "3. **Submit the review** with an overall summary:\n"
        "```\n"
        f"gh pr-review review submit -R {owner_repo} {number} \\\n"
        "  --body '<overall summary>' --event COMMENT\n"
        "```\n"
        "Use `--event APPROVE`, `COMMENT`, or `REQUEST_CHANGES` as appropriate.\n\n"
        "### Viewing existing reviews\n\n"
        "To check for existing review comments before posting:\n"
        "```\n"
        f"gh pr-review review view -R {owner_repo} {number} --unresolved\n"
        "```\n"
    )

    # Suggestion markdown
    sections.append(
        "## Suggestion Markdown\n\n"
        "When suggesting code fixes in review comments, use GitHub suggestion markdown.\n"
        "This allows the PR author to apply your fix directly from the review with one click.\n\n"
        "For a **single-line** suggestion, use:\n"
        "````\n"
        "```suggestion\n"
        "replacement code here\n"
        "```\n"
        "````\n\n"
        "For a **multi-line** suggestion (replacing lines start through end),\n"
        "add the comment on the ending line and use `--start-line` to specify the range:\n"
        "````\n"
        "```suggestion\n"
        "all replacement lines here\n"
        "```\n"
        "````\n\n"
        "The suggestion block replaces the line(s) the comment is attached to.\n"
        "Always prefer suggestion markdown over describing fixes in plain text.\n"
    )

    return "\n".join(sections)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Generate a code-review prompt for a GitHub pull request."
    )
    parser.add_argument(
        "pr_url",
        help="GitHub PR URL (e.g. https://github.com/owner/repo/pull/123)",
    )
    args = parser.parse_args()

    try:
        # Validate the URL early
        owner, repo, _number = parse_pr_url(args.pr_url)

        # Verify we're in the right git repo before attempting checkout
        validate_git_repo(owner, repo)

        # Checkout the PR branch so the AI reviewer can read local files
        checkout_pr(args.pr_url)

        # Fetch PR data
        metadata = fetch_pr_metadata(args.pr_url)

        # Scan for linked issues in PR body + branch name
        body = metadata.get("body", "") or ""
        branch = metadata.get("headRefName", "")
        search_text = f"{body}\n{branch}"

        jira_keys = find_jira_keys(search_text)
        sentry_urls = find_sentry_urls(body)

        # Gather context from extractors
        context = gather_context(jira_keys, sentry_urls)

        # Build and output the prompt
        prompt = build_prompt(metadata, context)
        print(prompt)

    except (ValueError, ReviewPRError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
