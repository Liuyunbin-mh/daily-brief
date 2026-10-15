"""Remember whether today's brief was sent, and which papers were seen."""

from __future__ import annotations

import json
from pathlib import Path

SEEN_LIMIT = 400
SUMMARY_LIMIT = 30


def load_state(path: Path) -> dict:
    empty = {"sent_on": "", "seen_arxiv_ids": [], "summaries": {}, "last_batch": []}
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    sent_on = data.get("sent_on") or ""
    seen = data.get("seen_arxiv_ids") or []
    summaries = data.get("summaries") or {}
    if not isinstance(seen, list):
        seen = []
    if not isinstance(summaries, dict):
        summaries = {}
    return {
        "sent_on": str(sent_on),
        "seen_arxiv_ids": [str(item) for item in seen],
        "summaries": {str(key): str(value) for key, value in summaries.items() if value},
        "last_batch": _clean_batch(data.get("last_batch")),
    }


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _clean_batch(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    papers = []
    for item in value:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        authors = item.get("authors") or []
        papers.append(
            {
                "id": str(item.get("id")),
                "title": str(item.get("title") or ""),
                "authors": [str(author) for author in authors if str(author).strip()]
                if isinstance(authors, list)
                else [],
                "published": str(item.get("published") or ""),
                "link": str(item.get("link") or ""),
                "query": str(item.get("query") or ""),
            }
        )
    return papers


def remember_ids(seen: list[str], new_ids: list[str], limit: int = SEEN_LIMIT) -> list[str]:
    fresh = [item for item in new_ids if item]
    fresh_set = set(fresh)
    ordered = [item for item in seen if item not in fresh_set]
    ordered.extend(fresh)
    return ordered[-limit:]


def remember_summaries(
    cached: dict[str, str],
    fresh: dict[str, str],
    keep_ids: list[str],
    limit: int = SUMMARY_LIMIT,
) -> dict[str, str]:
    merged = {key: value for key, value in cached.items() if value}
    merged.update({key: value for key, value in fresh.items() if value})
    ordered = [(item, merged[item]) for item in keep_ids if item in merged]
    return dict(ordered[-limit:])
