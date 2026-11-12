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
from typing import Dict, List, Pattern

# `default, web, ConfigMap (v1) has changed:` — the trailing verb tells us
# whether the resource was changed, created or deleted.
HEADER_RE = re.compile(
    r"^(?P<ns>[^,]*), (?P<name>[^,]+), (?P<kind>[\w.-]+) "
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
DRIFT_MARKER_TMPL = "<!-- helm-diff-comment:release={release} mode=drift -->"

MODE_UPGRADE = "upgrade"
MODE_DRIFT = "drift"


def marker(release: str, mode: str = MODE_UPGRADE) -> str:
    """The hidden HTML comment that makes a comment *sticky* per release.

    Drift reports get their own marker so a PR can carry both an upgrade diff
    and a drift report for the same release without one overwriting the other.
    """
    tmpl = DRIFT_MARKER_TMPL if mode == MODE_DRIFT else MARKER_TMPL
    return tmpl.format(release=release)


def render_comment(release: str, redacted_diff: str, report: Report,
                   fail_on_delete: bool = False, mode: str = MODE_UPGRADE,
                   revision: str = "",
                   managers: "Dict[tuple[str, str, str], List[FieldManager]] | None" = None,
                   ) -> str:
    """Render the full Markdown body posted to the PR.

    In drift mode the counts mean something else: ``changed`` is a live object
    that differs from the release manifest and ``added`` is an object the
    manifest has but the cluster does not. *managers* (drift mode only) maps
    ``(kind, namespace, name)`` to the non-Helm writers of that object.
    """
    drift = mode == MODE_DRIFT
    title = "Helm drift" if drift else "Helm diff"
    lines = [marker(release, mode), f"### 🔱 {title} — release `{release}`", ""]
    if drift:
        against = f"revision {revision}" if revision else "the current revision"
        lines += [f"Live objects compared with the manifest of {against}.", ""]
    if report.empty:
        if drift:
            lines.append("_No drift. Live objects match the release manifest._")
        else:
            lines.append("_No changes. The release is up to date._")
        return "\n".join(lines)

    if drift:
        lines.append(f"**{report.changed} drifted · {report.added} missing from the cluster**")
    else:
        lines.append(
            f"**{report.changed} changed · {report.added} added · "
            f"{report.removed} removed**"
        )
    if report.deletions:
        listed = ", ".join(f"`{k}/{n}`" for k, n in report.deletions)
        icon = "🛑" if fail_on_delete else "⚠️"
        verb = "blocks the merge" if fail_on_delete else "will be deleted"
        lines += ["", f"{icon} **{len(report.deletions)} resource(s) {verb}:** {listed}"]

    if drift and managers:
        lines += ["", "Written outside Helm (from `managedFields`, newest first):", ""]
        for (kind, ns, name), ms in managers.items():
            who = ", ".join(
                f"`{m.manager}` ({m.operation}{', ' + m.time if m.time else ''})"
                for m in ms
            )
            where = f"{ns}/" if ns else ""
            lines.append(f"- `{kind}/{where}{name}`: {who}")

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


# ---------------------------------------------------------------------------
# Drift mode: `kubectl diff` of the stored release manifest against the cluster
# ---------------------------------------------------------------------------

# kubectl diff writes each object to <tmp>/LIVE-<rand>/<obj> and
# <tmp>/MERGED-<rand>/<obj> and runs `diff -u -N` on the two directories, so
# every object block opens with a `--- .../LIVE-*/<obj>` line.
KUBECTL_FROM_RE = re.compile(r"^--- (?:\S*/)?LIVE-[^/\s]+/(?P<obj>\S+)(?:\s.*)?$")
KUBECTL_TO_RE = re.compile(r"^\+\+\+ (?:\S*/)?MERGED-[^/\s]+/(?P<obj>\S+)(?:\s.*)?$")
# <obj> is `[<group>.]<version>.<Kind>.<namespace>.<name>`; the namespace is
# empty for cluster-scoped objects. Namespaces cannot contain dots, names can.
KUBECTL_OBJ_RE = re.compile(
    r"^(?:(?P<group>.+?)\.)?(?P<version>v\d+(?:(?:alpha|beta)\d+)?)\."
    r"(?P<kind>[A-Z]\w*)\.(?P<ns>[^.]*)\.(?P<name>.+)$"
)
NEW_FILE_HUNK = "@@ -0,0 "


def parse_kubectl_object(obj: str) -> "tuple[str, str, str, str] | None":
    """Split a kubectl diff file name into (namespace, name, kind, apiVersion)."""
    m = KUBECTL_OBJ_RE.match(obj)
    if not m:
        return None
    group = m.group("group")
    api = f"{group}/{m.group('version')}" if group else m.group("version")
    return m.group("ns"), m.group("name"), m.group("kind"), api


def kubectl_diff_to_sections(diff: str) -> str:
    """Rewrite `kubectl diff` output into helm-diff's section format.

    Each object becomes a ``<ns>, <name>, <Kind> (<api>) has changed:`` block
    (or ``has been added:`` when the object is missing from the cluster, which
    shows up as a ``@@ -0,0`` hunk), so redaction, counting and rendering work
    the same as for an upgrade diff. ``-`` lines are the live object, ``+``
    lines what the release manifest says. ``diff ...`` and ``+++`` file lines
    are dropped; hunk headers are kept for orientation.
    """
    out: List[str] = []
    lines = diff.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = KUBECTL_FROM_RE.match(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if m and KUBECTL_TO_RE.match(nxt):
            parsed = parse_kubectl_object(m.group("obj"))
            ns, name, kind, api = parsed or ("", m.group("obj"), "Unknown", "")
            first_hunk = lines[i + 2] if i + 2 < len(lines) else ""
            verb = "been added" if first_hunk.startswith(NEW_FILE_HUNK) else "changed"
            out.append(f"{ns}, {name}, {kind} ({api}) has {verb}:")
            i += 2
            continue
        if line.startswith("diff ") and KUBECTL_FROM_RE.match(nxt):
            i += 1
            continue
        if out:
            out.append(line)
        i += 1
    return "\n".join(out) + ("\n" if out else "")


@dataclass
class FieldManager:
    manager: str
    operation: str
    time: str


def _is_helm_manager(name: str) -> bool:
    return name == "helm" or name.startswith("helm-")


def field_managers(live: object,
                   only: "set[tuple[str, str, str]] | None" = None,
                   ) -> "Dict[tuple[str, str, str], List[FieldManager]]":
    """Map each live object to the managers other than Helm that wrote it.

    *live* is the parsed ``kubectl get -o json --show-managed-fields`` output
    (a ``List`` or a single object). Entries for the ``status`` subresource are
    skipped: controllers update status all the time and that is not drift.
    *only* restricts the result to these ``(kind, namespace, name)`` keys.
    Managers are ordered newest first.
    """
    items: list = []
    if isinstance(live, dict):
        items = live.get("items") if live.get("kind") == "List" else [live]
    result: Dict["tuple[str, str, str]", List[FieldManager]] = {}
    for obj in items or []:
        if not isinstance(obj, dict):
            continue
        meta = obj.get("metadata") or {}
        key = (obj.get("kind", ""), meta.get("namespace", ""), meta.get("name", ""))
        if only is not None and key not in only:
            continue
        ms = []
        for e in meta.get("managedFields") or []:
            name = e.get("manager", "")
            if not name or _is_helm_manager(name) or e.get("subresource") == "status":
                continue
            ms.append(FieldManager(name, e.get("operation", ""), e.get("time", "")))
        if ms:
            ms.sort(key=lambda m: m.time, reverse=True)
            result[key] = ms
    return result
