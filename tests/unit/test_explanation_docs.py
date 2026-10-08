"""``docs/explanations.md`` examples validate with the assembler's contract check (task 1.8)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from finplan_agent.explanations.claims import CITATION_RE
from finplan_agent.explanations.result import validate_result
from finplan_agent.explanations.workflows import DISCLAIMER

DOC = Path(__file__).resolve().parents[2] / "docs" / "explanations.md"


def test_documented_result_example_validates_and_cites_its_evidence():
    text = DOC.read_text()
    m = re.search(r"<!-- example:explanation-result -->\s*```json\n(.*?)\n```", text, re.S)
    assert m, "the result example is missing"
    doc = json.loads(m.group(1))
    validate_result(doc)
    cites = {i["cite"] for i in doc["evidence_items"]}
    assert set(CITATION_RE.findall(doc["narrative"])) <= cites and doc["claim_check"]["passed"]
    assert DISCLAIMER in text and doc["disclaimer"] == DISCLAIMER
