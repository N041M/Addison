"""Both halves of a documentation gate: it must fire, and it must stay quiet.

A mutation test proves a gate **can** fail. Nothing in it proves the gate is
**silent** on prose a future document would legitimately contain — and that is
the property which decides whether the gate is still here next month. A gate
that cries wolf gets deleted by the next agent and takes its real coverage with
it, so a false positive costs more here than a missed one. That asymmetry is
already written into `doc_claims.py` ("if you cannot write a pattern that is
silent on today's tree, do not guess one") and into every dropped anchor in
`test_docs_drift.py`; this module is the part that *checks* it.

So a gate in `test_docs_drift.py` or `test_prompt_capability_claims.py` carries
two halves:

1. **it fires on the drift it was written for** — mutate the thing it guards and
   watch it go red; `docs/CONVENTIONS.md` owns that standard;
2. **it is silent on legitimate content** — asserted with `assert_silent` below.

## Write the gate as a scanner and the second half is free

The shape that makes this cheap: the check is a **scanner** — a plain function
from text to a list of findings — and the test is that scanner run over the
tree. A precision test is then the *same* scanner run over a sample. Write the
gate that way and both halves exist by construction. Write it as a loop inside a
test body and the precision half can only be had by copying the logic, which
tests the copy rather than the gate.

Samples are prose a real document would plausibly carry, lifted from the tree
where possible. An invented sentence nobody would ever write proves nothing; the
sample that matters is the one the next honest edit produces.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence


def assert_silent(
    gate: str,
    scan: Callable[[str], Sequence[object]],
    samples: dict[str, str],
) -> None:
    """Run ``scan`` over each sample and require that it finds nothing.

    ``samples`` maps *why this text is legitimate* to the text itself. The key is
    what a failure prints, so write it as the reason the gate would be wrong —
    "a backticked path that exists", not "sample 3".
    """
    noisy: list[str] = []
    for why, sample in samples.items():
        findings = list(scan(sample))
        if findings:
            noisy.append(
                f"{why}\n      sample : {sample.strip()[:180]}\n      flagged: {findings}"
            )
    assert not noisy, (
        f"`{gate}` fires on content it must NOT flag. Partial coverage people "
        "trust beats full coverage that gets disabled, so tighten the anchor — or "
        "drop it and say so in the gate's docstring:\n    " + "\n    ".join(noisy)
    )
