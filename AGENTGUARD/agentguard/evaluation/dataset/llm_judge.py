"""LLM-backed JudgeFn for GoldenDatasetValidator, built on the EXISTING
ModelProvider abstraction (agentguard/llm/provider.py) — the same "real
provider if configured, clearly-labeled deterministic stand-in
otherwise" rule the rest of the codebase already follows
(`get_default_provider()`), rather than inventing a second LLM-client
story just for dataset validation.

Note: `DeterministicTestProvider`'s canned JSON shape (label/score/
confidence/correctness/goal_completion/reason — built for the Decision
Engine's LLM-judge prompt) does not match this module's expected
classification/cited_evidence_ids/explanation shape. Paired with the
deterministic stand-in, `make_llm_judge` therefore always falls back to
NEEDS_REVIEW — conservative, never a fabricated VERIFIED, and a
correct reflection of "no real judge is actually configured."
"""
from __future__ import annotations

import json
import re
from typing import Any

from ...llm.provider import ModelProvider
from ...models import DatasetExample, Evidence
from .validator import JudgeVerdict

_PROMPT_TEMPLATE = """You are verifying a question/answer pair against retrieved evidence. \
Respond with ONLY a JSON object, no other text: \
{{"classification": one of VERIFIED, INCORRECT, UNSUPPORTED, AMBIGUOUS, OUTDATED, NEEDS_REVIEW, \
"cited_evidence_ids": [the evidence id(s) that support your classification], "explanation": a short reason}}. \
You MUST cite at least one evidence id if you classify as VERIFIED or INCORRECT — never classify \
either of those with an empty citation list.

Question: {question}
Proposed answer: {expected_answer}

Evidence:
{evidence_block}
"""


def _build_prompt(example: DatasetExample, evidence: list[Evidence]) -> str:
    evidence_block = "\n".join(f"[{e.id}] {e.text}" for e in evidence)
    return _PROMPT_TEMPLATE.format(question=example.question, expected_answer=example.expected_answer, evidence_block=evidence_block)


def _extract_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object found in judge response: {raw!r}")
    return json.loads(match.group(0))


def make_llm_judge(provider: ModelProvider):
    async def judge(example: DatasetExample, evidence: list[Evidence]) -> JudgeVerdict:
        prompt = _build_prompt(example, evidence)
        raw = await provider.complete(prompt)
        try:
            data = _extract_json(raw)
        except (ValueError, json.JSONDecodeError):
            return JudgeVerdict(classification="NEEDS_REVIEW", cited_evidence_ids=[], explanation=f"judge response was not valid JSON: {raw!r}")
        return JudgeVerdict(
            classification=data.get("classification") or "NEEDS_REVIEW",
            cited_evidence_ids=list(data.get("cited_evidence_ids") or []),
            explanation=str(data.get("explanation") or ""),
        )

    return judge
