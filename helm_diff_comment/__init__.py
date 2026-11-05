"""helm-diff-comment: turn `helm diff upgrade` output into a sticky PR comment.

The package is intentionally split so the interesting logic is pure and unit
tested:

* :mod:`helm_diff_comment.core` parses helm-diff output, redacts Secret values
  and renders the Markdown comment. No I/O.
* :mod:`helm_diff_comment.github` posts / updates the sticky comment. All HTTP
  goes through a single injectable transport so tests never touch the network.
* :mod:`helm_diff_comment.cli` wires the two together for the composite action.
"""

__version__ = "0.2.0"
