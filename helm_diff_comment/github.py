"""Post / update the sticky comment via the GitHub REST API.

Every HTTP call funnels through a single ``transport`` callable so unit tests
can drive the sticky-comment logic (find existing marker → PATCH, else POST)
with a fake in-memory GitHub instead of the network.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable, List, Optional

# transport(method, url, token, body|None) -> (status, parsed_json)
Transport = Callable[[str, str, str, Optional[dict]], "tuple[int, object]"]


def urllib_transport(method: str, url: str, token: str,
                     body: Optional[dict]) -> "tuple[int, object]":
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:  # noqa: S310 (fixed api host)
        payload = resp.read().decode() or "null"
        return resp.status, json.loads(payload)


class GitHubClient:
    """Minimal issue-comment client scoped to one repo + PR."""

    def __init__(self, repo: str, pr: int, token: str,
                 api: str = "https://api.github.com",
                 transport: Transport = urllib_transport) -> None:
        self.repo = repo
        self.pr = pr
        self.token = token
        self.api = api.rstrip("/")
        self._transport = transport

    def _list_comments(self) -> List[dict]:
        comments: List[dict] = []
        page = 1
        while True:
            url = (f"{self.api}/repos/{self.repo}/issues/{self.pr}"
                   f"/comments?per_page=100&page={page}")
            status, data = self._transport("GET", url, self.token, None)
            if status >= 300 or not isinstance(data, list) or not data:
                break
            comments.extend(data)
            if len(data) < 100:
                break
            page += 1
        return comments

    def find_sticky(self, marker: str) -> Optional[int]:
        """Return the id of the existing comment carrying *marker*, if any."""
        for c in self._list_comments():
            if marker in (c.get("body") or ""):
                return c.get("id")
        return None

    def upsert(self, marker: str, body: str) -> "tuple[str, int]":
        """Create or update the sticky comment; returns (action, comment_id)."""
        existing = self.find_sticky(marker)
        if existing is not None:
            url = f"{self.api}/repos/{self.repo}/issues/comments/{existing}"
            _, data = self._transport("PATCH", url, self.token, {"body": body})
            return "updated", data.get("id", existing) if isinstance(data, dict) else existing
        url = f"{self.api}/repos/{self.repo}/issues/{self.pr}/comments"
        _, data = self._transport("POST", url, self.token, {"body": body})
        cid = data.get("id", 0) if isinstance(data, dict) else 0
        return "created", cid


def pr_number_from_event() -> Optional[int]:
    """Read the PR number from the GitHub Actions event payload.

    Works for ``pull_request`` events (``event.pull_request.number``) and
    ``issue_comment`` events on PRs (``event.issue.number``).
    """
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return None
    with open(path) as fh:
        event = json.load(fh)
    pr = event.get("pull_request") or {}
    if pr.get("number"):
        return int(pr["number"])
    issue = event.get("issue") or {}
    if issue.get("number"):
        return int(issue["number"])
    return None
