import json
import os

import pytest

from helm_diff_comment import core

HERE = os.path.dirname(__file__)
FIXTURE = os.path.join(HERE, "..", "testdata", "changed.txt")


@pytest.fixture()
def diff():
    with open(FIXTURE, encoding="utf-8") as fh:
        return fh.read()


def test_split_sections_counts_kinds(diff):
    sections = core.split_sections(diff)
    kinds = [s.kind for s in sections]
    assert kinds == [
        "ConfigMap", "Secret", "Deployment",
        "Service", "HorizontalPodAutoscaler",
    ]


def test_analyze_counts_and_deletions(diff):
    r = core.analyze(diff)
    assert (r.changed, r.added, r.removed) == (3, 1, 1)
    assert r.total == 5
    assert r.deletions == [("Service", "web-old-cache")]
    assert not r.empty


def test_analyze_empty():
    r = core.analyze("")
    assert r.empty and r.total == 0 and r.deletions == []


def test_redact_masks_secret_values_only(diff):
    out = core.redact(diff)
    # Secret data values are gone...
    assert "b2xkcGFzcw==" not in out
    assert "bmV3cGFzcw==" not in out
    assert "c3VwZXItc2VjcmV0" not in out
    # ...but the keys are preserved so reviewers see *what* changed.
    assert "password: ***" in out
    assert "token: ***" in out
    # Non-secret values are untouched.
    assert "greeting: hola" in out
    assert "replicas: 4" in out


def test_redact_keeps_diff_markers(diff):
    out = core.redact(diff)
    assert "-   password: ***" in out
    assert "+   password: ***" in out
    assert "+   token: ***" in out


def test_configmap_data_is_not_treated_as_secret(diff):
    # `data:` also appears in the ConfigMap; only Secret sections get masked.
    out = core.redact(diff)
    assert "greeting: hello" in out
    assert "greeting: hola" in out


def test_extra_masks_apply_to_any_kind():
    diff = (
        "default, web, Deployment (apps/v1) has changed:\n"
        "-   value: AKIAIOSFODNN7EXAMPLE\n"
        "+   value: AKIAIOSFODNN7CHANGED\n"
    )
    out = core.redact(diff, extra_spec=r"AKIA[0-9A-Z]{16}")
    assert "AKIA" not in out
    assert out.count("***") == 2


def test_render_no_changes():
    body = core.render_comment("web", "", core.analyze(""))
    assert core.marker("web") in body
    assert "No changes" in body
    assert "```diff" not in body


def test_render_has_marker_summary_and_collapsible(diff):
    r = core.analyze(diff)
    body = core.render_comment("web", core.redact(diff), r)
    assert body.startswith(core.marker("web"))
    assert "3 changed · 1 added · 1 removed" in body
    assert "<details><summary>Show diff (5 resource(s))</summary>" in body
    assert "```diff" in body
    # deletion is surfaced
    assert "Service/web-old-cache" in body


def test_render_delete_gate_wording(diff):
    r = core.analyze(diff)
    soft = core.render_comment("web", core.redact(diff), r, fail_on_delete=False)
    hard = core.render_comment("web", core.redact(diff), r, fail_on_delete=True)
    assert "will be deleted" in soft and "⚠️" in soft
    assert "blocks the merge" in hard and "🛑" in hard


def test_marker_is_release_scoped():
    assert core.marker("web") != core.marker("api")
    assert "release=web" in core.marker("web")


def test_header_with_empty_namespace_is_parsed():
    diff = ", reader, ClusterRole (rbac.authorization.k8s.io/v1) has changed:\n-  - watch\n"
    (s,) = core.split_sections(diff)
    assert (s.namespace, s.name, s.kind) == ("", "reader", "ClusterRole")


def test_drift_marker_differs_from_upgrade_marker():
    up = core.marker("web")
    drift = core.marker("web", core.MODE_DRIFT)
    assert up != drift
    # The upgrade marker must not match inside a drift comment, or the two
    # sticky comments would overwrite each other.
    assert up not in drift


# --- drift mode --------------------------------------------------------------

DRIFT_FIXTURE = os.path.join(HERE, "..", "testdata", "drift.txt")
LIVE_FIXTURE = os.path.join(HERE, "..", "testdata", "drift-live.json")


@pytest.fixture()
def drift():
    with open(DRIFT_FIXTURE, encoding="utf-8") as fh:
        return core.kubectl_diff_to_sections(fh.read())


@pytest.mark.parametrize("obj,want", [
    ("apps.v1.Deployment.demo.demo", ("demo", "demo", "Deployment", "apps/v1")),
    ("v1.Service.demo.web", ("demo", "web", "Service", "v1")),
    ("rbac.authorization.k8s.io.v1.ClusterRole..demo.reader",
     ("", "demo.reader", "ClusterRole", "rbac.authorization.k8s.io/v1")),
    ("autoscaling.v2beta2.HorizontalPodAutoscaler.ns.web",
     ("ns", "web", "HorizontalPodAutoscaler", "autoscaling/v2beta2")),
    ("networking.k8s.io.v1.Ingress.ns.web.example.com",
     ("ns", "web.example.com", "Ingress", "networking.k8s.io/v1")),
])
def test_parse_kubectl_object(obj, want):
    assert core.parse_kubectl_object(obj) == want


def test_parse_kubectl_object_rejects_garbage():
    assert core.parse_kubectl_object("not-an-object") is None


def test_kubectl_diff_to_sections(drift):
    sections = core.split_sections(drift)
    assert [(s.kind, s.namespace, s.name, s.verb) for s in sections] == [
        ("Deployment", "demo", "demo", "changed"),
        ("Secret", "demo", "demo-creds", "changed"),
        ("ServiceAccount", "demo", "demo", "been added"),
        ("ClusterRole", "", "demo.reader", "changed"),
    ]
    # File header lines are dropped, the hunk content is kept.
    assert "LIVE-" not in drift and "MERGED-" not in drift
    assert "-  replicas: 3" in drift and "+  replicas: 1" in drift


def test_kubectl_diff_without_diff_lines():
    # Other diff tools may omit the `diff -u -N ...` line before the file pair.
    raw = (
        "--- /tmp/LIVE-1/v1.ConfigMap.ns.cfg\t2026-11-12 10:00:00 +0000\n"
        "+++ /tmp/MERGED-2/v1.ConfigMap.ns.cfg\t2026-11-12 10:00:00 +0000\n"
        "@@ -1,3 +1,3 @@\n"
        "-  a: 2\n"
        "+  a: 1\n"
    )
    (s,) = core.split_sections(core.kubectl_diff_to_sections(raw))
    assert (s.kind, s.name, s.verb) == ("ConfigMap", "cfg", "changed")


def test_kubectl_diff_empty():
    assert core.kubectl_diff_to_sections("") == ""
    assert core.analyze(core.kubectl_diff_to_sections("")).empty


def test_drift_redacts_secret_values(drift):
    out = core.redact(drift)
    assert "aGFuZGVkaXRlZA==" not in out
    assert "b3JpZ2luYWw=" not in out
    assert "-  password: ***" in out and "+  password: ***" in out


def test_field_managers_skip_helm_and_status():
    with open(LIVE_FIXTURE, encoding="utf-8") as fh:
        live = json.load(fh)
    ms = core.field_managers(live)
    dep = ms[("Deployment", "demo", "demo")]
    # Newest first; helm and the status subresource are left out.
    assert [(m.manager, m.time) for m in dep] == [
        ("kubectl-patch", "2026-11-12T10:01:00Z"),
        ("kube-controller-manager", "2026-11-12T09:58:05Z"),
    ]
    # Only helm wrote the ClusterRole, so it has no entry.
    assert ("ClusterRole", "", "demo.reader") not in ms


def test_field_managers_only_filter_and_single_object():
    with open(LIVE_FIXTURE, encoding="utf-8") as fh:
        live = json.load(fh)
    ms = core.field_managers(live, only={("Secret", "demo", "demo-creds")})
    assert list(ms) == [("Secret", "demo", "demo-creds")]
    single = core.field_managers(live["items"][1])
    assert single[("Secret", "demo", "demo-creds")][0].manager == "kubectl-edit"
    assert core.field_managers(None) == {}


def test_render_drift(drift):
    with open(LIVE_FIXTURE, encoding="utf-8") as fh:
        live = json.load(fh)
    r = core.analyze(drift)
    body = core.render_comment("demo", core.redact(drift), r,
                               mode=core.MODE_DRIFT, revision="4",
                               managers=core.field_managers(live))
    assert body.startswith(core.marker("demo", core.MODE_DRIFT))
    assert "Helm drift — release `demo`" in body
    assert "manifest of revision 4" in body
    assert "3 drifted · 1 missing from the cluster" in body
    assert "- `Deployment/demo/demo`: `kubectl-patch` (Update, 2026-11-12T10:01:00Z)" in body
    assert "- `Secret/demo/demo-creds`: `kubectl-edit`" in body


def test_render_drift_no_changes():
    body = core.render_comment("demo", "", core.analyze(""), mode=core.MODE_DRIFT)
    assert "No drift" in body
    assert "the current revision" in body
    assert "```diff" not in body
