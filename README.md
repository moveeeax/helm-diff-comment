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
| `chart`          | —                     | Chart path or reference (required).                |
| `namespace`      | `default`             | Release namespace.                                 |
| `values`         | `""`                  | Newline-separated values files; each becomes `-f`. |
| `helm-args`      | `""`                  | Extra raw args for `helm diff upgrade`.            |
| `mask-values`    | `""`                  | Comma-separated regexes; each match is masked.     |
| `fail-on-delete` | `false`               | Fail the job if any resource would be removed.     |
| `comment`        | `true`                | Post/update the sticky PR comment.                 |
| `github-token`   | `${{ github.token }}` | Token used to post the comment.                    |
| `helm-version`   | `v3.22.1`             | Helm version to install.                           |
| `helm-diff-version` | `v3.15.15`         | helm-diff plugin release tag to install.           |

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
