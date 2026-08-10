"""Pure helm-diff parsing, secret redaction and comment rendering.

The `databus23/helm-diff` plugin prints one block per changed resource. Each
block opens with a header line of the form::

    <namespace>, <name>, <Kind> (<apiVersion>) has changed:
    <namespace>, <name>, <Kind> (<apiVersion>) has been added:
    <namespace>, <name>, <Kind> (<apiVersion>) has been removed:

and is followed by a unified-diff-ish body whose lines start with a marker
column (``-``, ``+`` or a space) and then the rendered YAML. We anchor on the
header lines — they are the only reliable, stable part of the format — and treat
everything until the next header (or EOF) as that resource's body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Pattern

# `default, web, ConfigMap (v1) has changed:` — the trailing verb tells us
# whether the resource was changed, created or deleted.
HEADER_RE = re.compile(
    r"^(?P<ns>[^,]+), (?P<name>[^,]+), (?P<kind>[\w.-]+) "
    r"\((?P<api>[^)]*)\) has (?P<verb>changed|been added|been removed|been deleted):\s*$"
)

# A `key: value` mapping entry once the marker column is stripped.
KV_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9][\w./-]*): (?P<val>\S.*)$")
# `data:` / `stringData:` opens a block whose leaf values are secret material.
DATA_KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<key>data|stringData):\s*$")

REDACTED = "***"


@dataclass
class Section:
    """One changed resource inside a helm-diff report."""

    namespace: str
    name: str
    kind: str
    api: str
    verb: str  # "changed" | "been added" | "been removed" | "been deleted"
    lines: List[str] = field(default_factory=list)

    @property
    def is_delete(self) -> bool:
        return self.verb in ("been removed", "been deleted")

    @property
    def is_add(self) -> bool:
        return self.verb == "been added"

    @property
    def is_change(self) -> bool:
        return self.verb == "changed"


def _strip_marker(line: str) -> "tuple[str, str]":
    """Split a body line into its diff marker and the content after it.

    helm-diff bodies start every line with ``-``, ``+`` or a space. We keep the
    marker so a redacted line can be re-emitted unchanged everywhere except the
    value, and return the content for indentation-aware parsing.
    """
    if line and line[0] in "+- ":
        return line[0], line[1:]
    return "", line


def split_sections(diff: str) -> List[Section]:
    """Break raw helm-diff text into :class:`Section` objects."""
    sections: List[Section] = []
    current: Section | None = None
    for line in diff.splitlines():
        m = HEADER_RE.match(line)
        if m:
            current = Section(
                namespace=m.group("ns"),
                name=m.group("name"),
                kind=m.group("kind"),
                api=m.group("api"),
                verb=m.group("verb"),
            )
            sections.append(current)
            continue
        if current is not None:
            current.lines.append(line)
    return sections


def _redact_secret_lines(lines: List[str], extra: List[Pattern[str]]) -> List[str]:
    """Mask leaf values under a Secret's ``data:`` / ``stringData:`` block.

    We track the indentation of the ``data:`` key; any deeper ``key: value``
    entry is a secret leaf and gets its value replaced with ``***`` while the
    diff marker and key are preserved so the comment still shows *which* keys
    changed.
    """
    out: List[str] = []
    data_indent: int | None = None
    for line in lines:
        marker, content = _strip_marker(line)
        dm = DATA_KEY_RE.match(content)
        if dm:
            data_indent = len(dm.group("indent"))
            out.append(line)
            continue
        kv = KV_RE.match(content)
        if data_indent is not None and kv and len(kv.group("indent")) > data_indent:
            masked = f"{kv.group('indent')}{kv.group('key')}: {REDACTED}"
            out.append(f"{marker}{masked}")
            continue
        # A non-empty line at or above the data indent closes the block.
        if kv and len(kv.group("indent")) <= (data_indent if data_indent is not None else -1):
            data_indent = None
        out.append(line)
    return _apply_extra_masks(out, extra)


def _apply_extra_masks(lines: List[str], extra: List[Pattern[str]]) -> List[str]:
    if not extra:
        return lines
    out = []
    for line in lines:
        for pat in extra:
            line = pat.sub(REDACTED, line)
        out.append(line)
    return out


def compile_extra_masks(spec: str) -> List[Pattern[str]]:
    """Turn a comma-separated list of regexes into compiled patterns.

    Empty entries are ignored so ``mask-values: ""`` is a no-op. Each match is
    replaced wholesale with ``***`` — use a capture-free pattern for the value.
    """
    pats: List[Pattern[str]] = []
    for raw in spec.split(","):
        raw = raw.strip()
        if raw:
            pats.append(re.compile(raw))
    return pats


def redact(diff: str, extra_spec: str = "") -> str:
    """Return *diff* with every Secret value (and extra patterns) masked."""
    extra = compile_extra_masks(extra_spec)
    sections = split_sections(diff)
    if not sections:
        # No recognised sections: still honour explicit extra masks.
        return "\n".join(_apply_extra_masks(diff.splitlines(), extra))
    # Reconstruct: header line + (possibly redacted) body per section.
    out: List[str] = []
    for s in sections:
        out.append(f"{s.namespace}, {s.name}, {s.kind} ({s.api}) has {s.verb}:")
        body = s.lines
        if s.kind == "Secret":
            body = _redact_secret_lines(body, extra)
        else:
            body = _apply_extra_masks(body, extra)
        out.extend(body)
    return "\n".join(out)


@dataclass
class Report:
    changed: int
    added: int
    removed: int
    deletions: List["tuple[str, str]"]  # (kind, name)

    @property
    def total(self) -> int:
        return self.changed + self.added + self.removed

    @property
    def empty(self) -> bool:
        return self.total == 0


def analyze(diff: str) -> Report:
    """Count changed/added/removed resources and list deletions."""
    changed = added = removed = 0
    deletions: List["tuple[str, str]"] = []
    for s in split_sections(diff):
        if s.is_change:
            changed += 1
        elif s.is_add:
            added += 1
        elif s.is_delete:
            removed += 1
            deletions.append((s.kind, s.name))
    return Report(changed, added, removed, deletions)


MARKER_TMPL = "<!-- helm-diff-comment:release={release} -->"


def marker(release: str) -> str:
    """The hidden HTML comment that makes a comment *sticky* per release."""
    return MARKER_TMPL.format(release=release)


def render_comment(release: str, redacted_diff: str, report: Report,
                   fail_on_delete: bool = False) -> str:
    """Render the full Markdown body posted to the PR."""
    lines = [marker(release), f"### 🔱 Helm diff — release `{release}`", ""]
    if report.empty:
        lines.append("_No changes. The release is up to date._")
        return "\n".join(lines)

    lines.append(
        f"**{report.changed} changed · {report.added} added · "
        f"{report.removed} removed**"
    )
    if report.deletions:
        listed = ", ".join(f"`{k}/{n}`" for k, n in report.deletions)
        icon = "🛑" if fail_on_delete else "⚠️"
        verb = "blocks the merge" if fail_on_delete else "will be deleted"
        lines += ["", f"{icon} **{len(report.deletions)} resource(s) {verb}:** {listed}"]

    lines += [
        "",
        f"<details><summary>Show diff ({report.total} resource(s))</summary>",
        "",
        "```diff",
        redacted_diff.rstrip("\n"),
        "```",
        "",
        "</details>",
    ]
    return "\n".join(lines)
