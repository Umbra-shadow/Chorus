# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Guardianity — Chorus (Track 3)
"""Synthesis — the coordinator folds the orchestra into one build-grade document.

Executive summary, per-domain findings, the integrated build plan, the cost model
(from the finance agent), risks, and next steps. The whole document is committed
to the shared memory so the run can be questioned afterwards, and exported to PDF
via the copied Kioku report builder.
"""

from __future__ import annotations

import logging
from typing import Any

from engine.qwen import LLMError, QwenClient

from chorus.agent import DomainReport
from chorus.coordinator import Review, _reports_block

log = logging.getLogger("chorus.synthesis")

_SYNTH_SYSTEM = """\
You are the lead researcher writing the FINAL, unified report for a multi-domain
investigation. Integrate every domain's contribution into one coherent,
buildable answer to the original question — not a list of separate opinions.

Write in clean Markdown with these sections:
# <title>
## Executive summary
## The integrated solution   (how the domains combine into one buildable design)
## Findings by domain        (one short subsection per domain)
## Cost & feasibility        (use the finance domain's numbers)
## Risks & open problems
## Recommended next steps

Be concrete and honest. Where domains disagreed, state how you resolved it.
"""


async def synthesize(qwen: QwenClient, question: str, hypothesis: str,
                     reports: list[DomainReport], review: Review) -> str:
    agreements = "\n".join(f"- {a}" for a in review.agreements) or "(none captured)"
    conflicts = "\n".join(f"- {c.get('between')}: {c.get('issue')}" for c in review.conflicts) or "(none)"
    user = (
        f"ORIGINAL QUESTION:\n{question}\n\n"
        f"HYPOTHESIS:\n{hypothesis}\n\n"
        f"COORDINATOR NOTES:\nAgreements:\n{agreements}\nConflicts:\n{conflicts}\n\n"
        f"DOMAIN REPORTS:\n{_reports_block(reports)}"
    )
    try:
        report = (await qwen.chat(
            [{"role": "system", "content": _SYNTH_SYSTEM},
             {"role": "user", "content": user}],
            temperature=0.35, max_tokens=8192,
        )).strip()
    except LLMError as e:
        log.warning("synthesis failed: %s", e)
        report = f"# {question}\n\n_Synthesis unavailable: {e}_"
    return report


def build_pdf_bytes(public_run: dict[str, Any]) -> bytes:
    """Export the final document to PDF, reusing the copied Kioku report builder."""
    from engine.research.report_pdf import build_pdf
    return build_pdf({
        "topic": public_run.get("question", "Chorus report"),
        "report": public_run.get("report", ""),
        "findings": [],
        "grounded_count": public_run.get("grounded_total", 0),
        "provider": "chorus",
    })
