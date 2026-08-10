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
