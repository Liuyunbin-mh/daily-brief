"""Plain-text brief assembly and the local Markdown copy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class Section:
    title: str
    ok: bool
    body: str


def render(sections: list[Section], day: date) -> str:
    parts = [f"# 早报 {day.isoformat()}", ""]
    for section in sections:
        parts.append(f"## {section.title}")
        parts.append(section.body.rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def subject_for(day: date) -> str:
    return f"[早报] {day.isoformat()}"


def write_report(directory: Path, day: date, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}.md"
    path.write_text(body, encoding="utf-8")
    return path
