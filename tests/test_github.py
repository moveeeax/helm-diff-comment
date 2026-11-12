import json
import os

from helm_diff_comment import github


class FakeGitHub:
    """In-memory stand-in for the issue-comments API."""

    def __init__(self, existing=None):
        self.comments = list(existing or [])
        self._next = 1000
        self.calls = []

    def transport(self, method, url, token, body):
        self.calls.append((method, url, body))
        if method == "GET":
            return 200, list(self.comments)
        if method == "POST":
            self._next += 1
            c = {"id": self._next, "body": body["body"],
                 "html_url": f"https://github.com/o/r/pull/7#issuecomment-{self._next}"}
            self.comments.append(c)
            return 201, c
        if method == "PATCH":
            cid = int(url.rstrip("/").split("/")[-1])
            for c in self.comments:
                if c["id"] == cid:
                    c["body"] = body["body"]
                    return 200, c
            return 404, {}
        raise AssertionError(method)


def make_client(fake, repo="moveeeax/demo", pr=7):
    return github.GitHubClient(repo, pr, "tok", transport=fake.transport)


def test_upsert_creates_when_absent():
    fake = FakeGitHub()
    client = make_client(fake)
    action, cid, url = client.upsert("<!-- m -->", "<!-- m -->\nhello")
    assert action == "created"
    assert url == f"https://github.com/o/r/pull/7#issuecomment-{cid}"
    assert any(m == "POST" for m, _, _ in fake.calls)
    assert fake.comments[-1]["body"].endswith("hello")


def test_upsert_updates_existing_marker():
    fake = FakeGitHub(existing=[
        {"id": 1, "body": "unrelated"},
        {"id": 2, "body": "<!-- m -->\nold", "html_url": "https://x/2"},
    ])
    client = make_client(fake)
    action, cid, url = client.upsert("<!-- m -->", "<!-- m -->\nnew")
    assert action == "updated"
    assert cid == 2
    assert url == "https://x/2"
    assert fake.comments[1]["body"].endswith("new")
    # no second comment created
    assert len(fake.comments) == 2


def test_find_sticky_none():
    fake = FakeGitHub(existing=[{"id": 1, "body": "nope"}])
    assert make_client(fake).find_sticky("<!-- m -->") is None


def test_two_releases_are_independent():
    fake = FakeGitHub()
    web = make_client(fake)
    api = make_client(fake)
    web.upsert("<!-- release=web -->", "<!-- release=web -->\nA")
    api.upsert("<!-- release=api -->", "<!-- release=api -->\nB")
    assert len(fake.comments) == 2
    # updating web keeps api intact
    web.upsert("<!-- release=web -->", "<!-- release=web -->\nA2")
    bodies = sorted(c["body"] for c in fake.comments)
    assert bodies == ["<!-- release=api -->\nB", "<!-- release=web -->\nA2"]


def test_pr_number_from_event(tmp_path, monkeypatch):
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"pull_request": {"number": 42}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(ev))
    assert github.pr_number_from_event() == 42


def test_pr_number_from_issue_comment_event(tmp_path, monkeypatch):
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"issue": {"number": 99}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(ev))
    assert github.pr_number_from_event() == 99


def test_pr_number_missing(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert github.pr_number_from_event() is None
