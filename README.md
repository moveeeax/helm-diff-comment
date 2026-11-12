# helm-diff-comment

[![ci](https://github.com/moveeeax/helm-diff-comment/actions/workflows/ci.yml/badge.svg)](https://github.com/moveeeax/helm-diff-comment/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

A composite GitHub Action that runs `helm diff upgrade` on a pull request and
posts the result as a **sticky, collapsible PR comment** — with Secret values
redacted and an optional gate on resource deletions. Review what a chart change
will actually do to the cluster *before* you merge, without leaving the PR.

## What it does

- Runs the [`helm-diff`](https://github.com/databus23/helm-diff) plugin against
  the target release and renders the report into the PR.
- **Sticky per release:** one comment per `release`, updated in place on every
  push instead of piling up new comments. Multiple releases (matrix) each get
  their own comment.
- **Redacts secrets:** every value inside a `Secret`'s `data:` / `stringData:`
  block is replaced with `***` while the keys stay visible, so reviewers see
  *which* secret changed without leaking the material. Extra regex masks
  (API keys, tokens) can be added with `mask-values`.
- **Deletion gate (optional):** with `fail-on-delete: true` the job fails if the
  upgrade would remove any resource, and the removed resources are called out in
  the comment.
- **Drift check (`mode: drift`):** compares the manifest stored in the release
  with the live objects and reports what was changed out of band since the last
  `helm upgrade` (a `kubectl edit`, a `kubectl scale`, a deleted object), with
  the non-Helm writer of each drifted object taken from `managedFields`. See
  [Drift mode](#drift-mode).
- **Step outputs:** counts, a `has-changes` flag, the deleted resources and the
  comment URL, so later steps can label the PR or require an extra approval.

## How it works

The action is a thin composite wrapper — install Helm, install the helm-diff
plugin, capture `helm diff upgrade --no-color`, then hand the text to a small
Python core that does the real work:

- **Anchor on the header lines.** helm-diff opens each resource block with
  `<ns>, <name>, <Kind> (<api>) has changed|been added|been removed:`. That line
  is the only stable part of the format, so parsing keys off it and treats
  everything until the next header as the resource body.
- **Indentation-aware redaction.** Inside a `Secret` section the parser tracks
  the indentation of the `data:` key and masks only the deeper `key: value`
  leaves — so a `ConfigMap`'s `data:` (or a metadata field named `data`) is never
  touched.

The parsing, redaction and comment rendering are pure functions with no I/O, and
the GitHub API calls go through a single injectable transport, so the whole thing
is unit tested without a cluster or the network.

## Usage

```yaml
- uses: moveeeax/helm-diff-comment@v0
  with:
    release: web
    chart: ./charts/web
    namespace: staging
    values: envs/staging/web.yaml
    fail-on-delete: "true"
```

`permissions: pull-requests: write` is required for the comment. A full
matrix-over-environments workflow is in [`examples/multi-env.yml`](./examples/multi-env.yml).

Rendered comment (from the test fixture):

```console
$ PYTHONPATH=. python3 -m helm_diff_comment \
    --release web --diff-file testdata/changed.txt --out comment.md
$ head -6 comment.md
<!-- helm-diff-comment:release=web -->
### 🔱 Helm diff — release `web`

**3 changed · 1 added · 1 removed**

⚠️ **1 resource(s) will be deleted:** `Service/web-old-cache`
```

### Inputs

| Input            | Default               | Description                                        |
| ---------------- | --------------------- | -------------------------------------------------- |
| `release`        | —                     | Helm release name (required).                      |
| `chart`          | `""`                  | Chart path or reference. Required in `upgrade` mode, unused in `drift`. |
| `mode`           | `upgrade`             | `upgrade` (`helm diff upgrade`) or `drift` (live objects vs. release manifest). |
| `revision`       | `""`                  | Drift mode: release revision to compare with; empty means current. |
| `namespace`      | `default`             | Release namespace.                                 |
| `values`         | `""`                  | Newline-separated values files; each becomes `-f`. |
| `helm-args`      | `""`                  | Extra raw args for `helm diff upgrade`.            |
| `mask-values`    | `""`                  | Comma-separated regexes; each match is masked.     |
| `fail-on-delete` | `false`               | Fail the job if any resource would be removed (exit 2). |
| `fail-on-changes` | `false`              | Fail the job if any resource differs (exit 3).     |
| `comment`        | `true`                | Post/update the sticky PR comment.                 |
| `github-token`   | `${{ github.token }}` | Token used to post the comment.                    |
| `helm-version`   | `v3.22.1`             | Helm version to install.                           |
| `helm-diff-version` | `v3.15.15`         | helm-diff plugin release tag to install.           |

### Outputs

| Output              | Example                       | Description                                              |
| ------------------- | ----------------------------- | -------------------------------------------------------- |
| `changed`           | `3`                           | Resources that change. In drift mode, resources that drifted. |
| `added`             | `1`                           | Resources added. In drift mode, release objects missing from the cluster. |
| `removed`           | `1`                           | Resources removed. Always `0` in drift mode.             |
| `total`             | `5`                           | `changed + added + removed`.                             |
| `has-changes`       | `true`                        | `true` if `total` is above 0, else `false`.              |
| `deleted-resources` | `["Service/web-old-cache"]`   | JSON list of removed resources as `Kind/name`.           |
| `comment-url`       | `https://github.com/…#issuecomment-1` | URL of the sticky comment; empty if none was posted. |

Outputs are written before the `fail-on-delete` / `fail-on-changes` gates
exit, so an `if: failure()` step can still read them.

```yaml
- id: diff
  uses: moveeeax/helm-diff-comment@v0
  with:
    release: web
    chart: ./charts/web
- if: steps.diff.outputs.has-changes == 'false'
  run: gh pr edit "$PR" --add-label helm:no-op
  env:
    GH_TOKEN: ${{ github.token }}
    PR: ${{ github.event.pull_request.number }}
- if: steps.diff.outputs.deleted-resources != '[]'
  run: echo "::warning::deletes ${{ steps.diff.outputs.deleted-resources }}"
```

Helm 3 and Helm 4 both work (CI runs the action on each). On Helm 4 the plugin
is installed with `--verify=false`, because Helm 4 cannot verify a plugin
installed from a git URL; the pinned `helm-diff-version` tag is what keeps the
install reproducible.

## Drift mode

`mode: drift` answers "what did someone change in the cluster since the last
`helm upgrade`?" instead of "what will this chart change do?". The action runs:

1. `helm get manifest <release> [--revision N]` to get the manifest Helm stored
   for the release;
2. `kubectl diff --server-side --force-conflicts -f <manifest>`, a server-side
   dry-run apply of that manifest. The API server fills in its defaults and
   leaves `status` alone, so the diff only shows fields the manifest sets whose
   live value is different. `--force-conflicts` lets the dry run take over
   fields another manager now owns, which is the drift itself;
3. `kubectl get -f <manifest> -o json --show-managed-fields` to list who other
   than Helm wrote each drifted object (`status` subresource writes are left
   out).

In the comment, `-` lines are the live object and `+` lines what the release
manifest says, i.e. what a fresh `helm upgrade` would put back. A release object
missing from the cluster is counted as `added` ("missing from the cluster").

```yaml
on:
  schedule:
    - cron: "0 6 * * *"
jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      # ...configure cluster credentials first
      - uses: moveeeax/helm-diff-comment@v0
        with:
          release: ingress-nginx
          namespace: ingress
          mode: drift
          comment: "false"        # no PR on a schedule; the report goes to the step summary
          fail-on-changes: "true" # red run when anything drifted
```

Limits, by design of server-side apply: a field that the manifest does not set
(an extra label or annotation added by hand) is not reported, and objects
created by hand outside the release are not seen. Fields a controller owns on
purpose (for example `spec.replicas` under an HPA) are reported as drift, so
leave such fields out of the chart. Drift mode needs
`kubectl` on the runner (GitHub-hosted Ubuntu runners have it) and RBAC to `get`
and dry-run `patch` the release objects; it does not install the helm-diff
plugin.

## One note

`helm diff upgrade` with `--detailed-exitcode` returns a non-zero exit when there
*are* changes — which is exactly the case you care about. The action captures the
diff with `set +e` so a "there are changes" exit never aborts the step; the only
thing that can fail the job is the explicit `fail-on-delete` gate. Keeping "the
tool worked" and "the tool found something bad" as separate signals is what makes
the gate trustworthy.

## Development

```console
$ python3 -m venv .venv && . .venv/bin/activate
$ pip install pytest
$ PYTHONPATH=. python -m pytest -q
```

MIT licensed — see [LICENSE](./LICENSE).

<https://github.com/moveeeax/helm-diff-comment>
