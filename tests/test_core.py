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
