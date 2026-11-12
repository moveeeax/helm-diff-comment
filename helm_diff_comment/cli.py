"""Command-line entrypoint used by the composite action.

Reads a helm-diff report (or, with ``--mode drift``, a ``kubectl diff`` of the
release manifest against the cluster) from a file, redacts secrets, renders the
sticky comment, optionally posts it to the PR, writes the counts to
``$GITHUB_OUTPUT``, and exits non-zero when ``--fail-on-delete`` is set and the
diff removes resources, or ``--fail-on-changes`` is set and anything differs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import core, github

EXIT_OK = 0
EXIT_DELETE = 2
EXIT_CHANGES = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="helm-diff-comment")
    p.add_argument("--release", required=True)
    p.add_argument("--namespace", default="default")
    p.add_argument("--diff-file", required=True)
    p.add_argument("--mode", choices=[core.MODE_UPGRADE, core.MODE_DRIFT],
                   default=core.MODE_UPGRADE,
                   help="upgrade: --diff-file is `helm diff upgrade` output; "
                        "drift: --diff-file is `kubectl diff` of the release manifest")
    p.add_argument("--live-file", default="",
                   help="drift mode: `kubectl get -o json --show-managed-fields` "
                        "of the release objects, used to name who changed them")
    p.add_argument("--revision", default="",
                   help="drift mode: release revision the manifest came from")
    p.add_argument("--mask-values", default="",
                   help="comma-separated regexes; each match is masked")
    p.add_argument("--fail-on-delete", action="store_true")
    p.add_argument("--fail-on-changes", action="store_true",
                   help="exit non-zero if any resource differs")
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

    managers = None
    if args.mode == core.MODE_DRIFT:
        diff = core.kubectl_diff_to_sections(diff)
        managers = _load_managers(args.live_file, diff)

    redacted = core.redact(diff, args.mask_values)
    report = core.analyze(diff)
    body = core.render_comment(args.release, redacted, report, args.fail_on_delete,
                               mode=args.mode, revision=args.revision,
                               managers=managers)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body)

    if args.mode == core.MODE_DRIFT:
        summary = (f"{args.release}: {report.changed} drifted, {report.added} "
                   f"missing from the cluster")
    else:
        summary = (f"{args.release}: {report.changed} changed, {report.added} added, "
                   f"{report.removed} removed")
    print(summary, file=sys.stderr)
    _emit_step_summary(body)

    comment_url = ""
    if args.comment:
        comment_url = _post(args, body)
    _emit_outputs(report, comment_url)

    if args.fail_on_delete and report.deletions:
        listed = ", ".join(f"{k}/{n}" for k, n in report.deletions)
        print(f"error: refusing to proceed, {len(report.deletions)} resource(s) "
              f"would be deleted: {listed}", file=sys.stderr)
        return EXIT_DELETE
    if args.fail_on_changes and not report.empty:
        print(f"error: {report.total} resource(s) differ", file=sys.stderr)
        return EXIT_CHANGES
    return EXIT_OK


def _load_managers(path: str, sections_diff: str):
    """Read the live objects and keep the non-Helm writers of drifted ones."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        raw = fh.read().strip()
    if not raw:
        return None
    try:
        live = json.loads(raw)
    except ValueError:
        print(f"warning: {path} is not JSON, skipping field managers", file=sys.stderr)
        return None
    drifted = {(s.kind, s.namespace, s.name)
               for s in core.split_sections(sections_diff) if s.is_change}
    return core.field_managers(live, only=drifted)


def _post(args: argparse.Namespace, body: str) -> str:
    """Upsert the sticky comment; returns its URL, or "" when skipped."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token or not args.repo:
        print("warning: no GITHUB_TOKEN/repo, skipping comment", file=sys.stderr)
        return ""
    pr = args.pr or github.pr_number_from_event() or 0
    if not pr:
        print("warning: no PR number in context, skipping comment", file=sys.stderr)
        return ""
    client = github.GitHubClient(args.repo, pr, token)
    action, cid, url = client.upsert(core.marker(args.release, args.mode), body)
    print(f"{action} sticky comment #{cid} on PR #{pr}", file=sys.stderr)
    return url


def outputs(report: core.Report, comment_url: str = "") -> "dict[str, str]":
    """The step outputs, as the strings written to ``$GITHUB_OUTPUT``."""
    return {
        "changed": str(report.changed),
        "added": str(report.added),
        "removed": str(report.removed),
        "total": str(report.total),
        "has-changes": "false" if report.empty else "true",
        "deleted-resources": json.dumps([f"{k}/{n}" for k, n in report.deletions]),
        "comment-url": comment_url,
    }


def _emit_outputs(report: core.Report, comment_url: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for k, v in outputs(report, comment_url).items():
            # Every value is a single line (JSON has no raw newlines here),
            # so the plain `name=value` form is safe.
            fh.write(f"{k}={v}\n")


def _emit_step_summary(body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(body + "\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
