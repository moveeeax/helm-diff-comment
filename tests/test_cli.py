import os

from helm_diff_comment import cli

HERE = os.path.dirname(__file__)
FIXTURE = os.path.join(HERE, "..", "testdata", "changed.txt")


def test_cli_writes_comment_and_exits_ok(tmp_path):
    out = tmp_path / "comment.md"
    rc = cli.run([
        "--release", "web",
        "--diff-file", FIXTURE,
        "--out", str(out),
    ])
    assert rc == cli.EXIT_OK
    body = out.read_text()
    assert body.startswith("<!-- helm-diff-comment:release=web -->")
    assert "b2xkcGFzcw==" not in body  # secret masked
    assert "password: ***" in body


def test_cli_fail_on_delete_exit_code(tmp_path):
    out = tmp_path / "c.md"
    rc = cli.run([
        "--release", "web",
        "--diff-file", FIXTURE,
        "--fail-on-delete",
        "--out", str(out),
    ])
    assert rc == cli.EXIT_DELETE


def test_cli_no_delete_no_gate(tmp_path):
    diff = tmp_path / "d.txt"
    diff.write_text(
        "default, web, ConfigMap (v1) has changed:\n"
        "-   a: 1\n+   a: 2\n"
    )
    rc = cli.run([
        "--release", "web",
        "--diff-file", str(diff),
        "--fail-on-delete",
    ])
    assert rc == cli.EXIT_OK


def test_cli_extra_mask(tmp_path):
    diff = tmp_path / "d.txt"
    diff.write_text(
        "default, web, Deployment (apps/v1) has changed:\n"
        "+   token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n"
    )
    out = tmp_path / "c.md"
    cli.run([
        "--release", "web",
        "--diff-file", str(diff),
        "--mask-values", r"ghp_[A-Za-z0-9]{36}",
        "--out", str(out),
    ])
    assert "ghp_" not in out.read_text()


DRIFT_FIXTURE = os.path.join(HERE, "..", "testdata", "drift.txt")
LIVE_FIXTURE = os.path.join(HERE, "..", "testdata", "drift-live.json")


def read_outputs(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_cli_writes_github_outputs(tmp_path, monkeypatch):
    gh_out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    rc = cli.run(["--release", "web", "--diff-file", FIXTURE, "--fail-on-delete"])
    # Outputs are written even when the deletion gate fails the step, so a
    # later `if: failure()` step can still read them.
    assert rc == cli.EXIT_DELETE
    assert read_outputs(gh_out) == {
        "changed": "3",
        "added": "1",
        "removed": "1",
        "total": "5",
        "has-changes": "true",
        "deleted-resources": '["Service/web-old-cache"]',
        "comment-url": "",
    }


def test_cli_outputs_no_changes(tmp_path, monkeypatch):
    gh_out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    diff = tmp_path / "d.txt"
    diff.write_text("")
    assert cli.run(["--release", "web", "--diff-file", str(diff)]) == cli.EXIT_OK
    got = read_outputs(gh_out)
    assert got["has-changes"] == "false"
    assert got["total"] == "0"
    assert got["deleted-resources"] == "[]"


def test_cli_outputs_comment_url(tmp_path, monkeypatch):
    gh_out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")

    seen = {}

    class FakeClient:
        def __init__(self, repo, pr, token):
            seen["repo"], seen["pr"] = repo, pr

        def upsert(self, marker, body):
            seen["marker"] = marker
            return "created", 5, "https://github.com/o/r/pull/7#issuecomment-5"

    monkeypatch.setattr(cli.github, "GitHubClient", FakeClient)
    cli.run(["--release", "web", "--diff-file", FIXTURE,
             "--comment", "--repo", "o/r", "--pr", "7"])
    assert seen == {"repo": "o/r", "pr": 7, "marker": cli.core.marker("web")}
    assert read_outputs(gh_out)["comment-url"] == \
        "https://github.com/o/r/pull/7#issuecomment-5"


def test_cli_fail_on_changes(tmp_path):
    assert cli.run(["--release", "web", "--diff-file", FIXTURE,
                    "--fail-on-changes"]) == cli.EXIT_CHANGES
    empty = tmp_path / "d.txt"
    empty.write_text("")
    assert cli.run(["--release", "web", "--diff-file", str(empty),
                    "--fail-on-changes"]) == cli.EXIT_OK


def test_cli_drift_mode(tmp_path, monkeypatch):
    gh_out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    out = tmp_path / "c.md"
    rc = cli.run([
        "--release", "demo",
        "--mode", "drift",
        "--diff-file", DRIFT_FIXTURE,
        "--live-file", LIVE_FIXTURE,
        "--revision", "2",
        "--fail-on-changes",
        "--out", str(out),
    ])
    assert rc == cli.EXIT_CHANGES
    body = out.read_text()
    assert body.startswith(cli.core.marker("demo", "drift"))
    assert "3 drifted · 1 missing from the cluster" in body
    assert "`kubectl-patch`" in body
    # The Service is not in the diff, so its manager is not listed.
    assert "kubectl-annotate" not in body
    assert "aGFuZGVkaXRlZA==" not in body
    got = read_outputs(gh_out)
    assert (got["changed"], got["added"], got["removed"]) == ("3", "1", "0")


def test_cli_drift_mode_bad_live_file(tmp_path):
    live = tmp_path / "live.json"
    live.write_text("Error from server (Forbidden)")
    out = tmp_path / "c.md"
    rc = cli.run(["--release", "demo", "--mode", "drift",
                  "--diff-file", DRIFT_FIXTURE, "--live-file", str(live),
                  "--out", str(out)])
    assert rc == cli.EXIT_OK
    assert "Written outside Helm" not in out.read_text()
