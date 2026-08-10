# Examples

- [`multi-env.yml`](./multi-env.yml) — matrix over environments, one sticky
  comment per release, gate on deletions, extra secret masks.

The fixture under [`../testdata/changed.txt`](../testdata/changed.txt) is a real
`helm diff upgrade --no-color` report used by the tests and the CI smoke run. To
see the rendered comment locally:

```console
$ PYTHONPATH=. python3 -m helm_diff_comment \
    --release web --diff-file testdata/changed.txt --out /tmp/comment.md
$ cat /tmp/comment.md
```
