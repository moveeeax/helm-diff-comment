# helm-diff-comment

> See exactly what a Helm upgrade will change — in the PR.

**Status:** 🚧 In development

## Overview

Post `helm diff upgrade` output as a PR comment before merging chart changes.

## Features

- Runs helm-diff against the target release
- Sticky, collapsible PR comment
- Redacts secret values
- Works with multiple releases/environments
- Fails gate on unexpected resource deletions (optional)

## Stack

Composite Action + helm + helm-diff plugin.

## Usage

```yaml
- uses: cybercapybara/helm-diff-comment@v1
  with:
    release: web
    chart: ./charts/web
```

## License

MIT
