"""Command-line entrypoint used by the composite action.

Reads a helm-diff report from a file, redacts secrets, renders the sticky
comment, optionally posts it to the PR, and exits non-zero when
``--fail-on-delete`` is set and the diff removes resources.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import core, github

EXIT_OK = 0
EXIT_DELETE = 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="helm-diff-comment")
    p.add_argument("--release", required=True)
    p.add_argument("--namespace", default="default")
    p.add_argument("--diff-file", required=True)
    p.add_argument("--mask-values", default="",
                   help="comma-separated regexes; each match is masked")
    p.add_argument("--fail-on-delete", action="store_true")
    p.add_argument("--comment", action="store_true",
                   help="post the sticky PR comment (needs GITHUB_TOKEN)")
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    p.add_argument("--pr", type=int, default=0)
    p.add_argument("--out", default="",
                   help="also write the rendered comment to this file")
    return p


def run(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    with open(args.diff_file, encoding="utf-8", errors="replace") as fh:
        diff = fh.read()

    redacted = core.redact(diff, args.mask_values)
    report = core.analyze(diff)
    body = core.render_comment(args.release, redacted, report, args.fail_on_delete)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body)

    summary = (f"{args.release}: {report.changed} changed, {report.added} added, "
               f"{report.removed} removed")
    print(summary, file=sys.stderr)
    _emit_step_summary(body)

    if args.comment:
        _post(args, body)

    if args.fail_on_delete and report.deletions:
        listed = ", ".join(f"{k}/{n}" for k, n in report.deletions)
        print(f"error: refusing to proceed, {len(report.deletions)} resource(s) "
              f"would be deleted: {listed}", file=sys.stderr)
        return EXIT_DELETE
    return EXIT_OK


def _post(args: argparse.Namespace, body: str) -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token or not args.repo:
        print("warning: no GITHUB_TOKEN/repo, skipping comment", file=sys.stderr)
        return
    pr = args.pr or github.pr_number_from_event() or 0
    if not pr:
        print("warning: no PR number in context, skipping comment", file=sys.stderr)
        return
    client = github.GitHubClient(args.repo, pr, token)
    action, cid = client.upsert(core.marker(args.release), body)
    print(f"{action} sticky comment #{cid} on PR #{pr}", file=sys.stderr)


def _emit_step_summary(body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(body + "\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
