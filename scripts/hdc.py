#!/usr/bin/env python3
"""Thin launcher so the composite action can call the package by file path.

`action.yml` runs `python3 <action_path>/scripts/hdc.py ...`; we add the action
root to `sys.path` and defer to :func:`helm_diff_comment.cli.run`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from helm_diff_comment.cli import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
