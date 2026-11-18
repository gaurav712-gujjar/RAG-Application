import json
from dataclasses import dataclass
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden"


@dataclass
class QA:
    id: str
    tag: str
    question: str
    answerable: bool
    ground_truth: str
    source: str | None = None
    evidence: str | None = None
    key_fact: str | None = None


def load_qa(path: Path | None = None) -> list[QA]:
    path = path or GOLDEN / "qa.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [QA(**r) for r in rows]


def doc_paths(directory: Path | None = None) -> list[Path]:
    return sorted((directory or GOLDEN / "docs").glob("*.md"))
