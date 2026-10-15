"""Latest arXiv metadata. PDFs are never downloaded."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from daily_brief.mathtext import to_plain_math
from daily_brief.report import Section

_ATOM = "{http://www.w3.org/2005/Atom}"
_QUERY_PAUSE_SECONDS = 3


def collect_arxiv(
    *,
    queries: list[str],
    max_results: int,
    seen_ids: list[str],
    fetch_bytes,
    sleep,
    summaries: dict[str, str] | None = None,
) -> tuple[Section, list[str], list[dict], list[str]]:
    selected = [query for query in queries if query.strip()][:3]
    if not selected:
        return Section("论文", False, "本次未获取：配置中没有 arXiv 检索式"), [], [], []
    seen = set(seen_ids)
    errors: list[str] = []
    papers: list[dict] = []
    for index, query in enumerate(selected):
        if index:
            sleep(_QUERY_PAUSE_SECONDS)
        try:
            found = parse_feed(fetch_bytes(_query_url(query, max_results)))
        except Exception as exc:
            errors.append(f"{query}：{exc}")
            continue
        if not found:
            papers.append({"query": query, "empty": True, "id": "", "is_new": False})
            continue
        for paper in found:
            paper["query"] = query
            paper["is_new"] = paper["id"] not in seen
            papers.append(paper)
    section = render_papers(papers, summaries or {}, errors)
    return section, [paper["id"] for paper in papers if paper.get("id")], papers, errors


def unseen_papers(records: list[dict], seen_ids: list[str]) -> tuple[list[dict], bool]:
    """Papers not yet pushed, in feed order. The flag is whether the feed had any real entry."""
    seen = set(seen_ids)
    fresh: list[dict] = []
    used: set[str] = set()
    real = False
    for paper in records:
        paper_id = str(paper.get("id") or "")
        if paper.get("empty") or not paper_id:
            continue
        real = True
        if paper_id in used or paper_id in seen:
            continue
        used.add(paper_id)
        fresh.append({**paper, "is_new": True})
    return fresh, real


def fallback_section(last_batch: list[dict], summaries: dict[str, str], errors: list[str]) -> Section:
    papers = [{**paper, "is_new": False} for paper in last_batch if paper.get("id")]
    if not papers:
        note = "今日没有新论文。"
        if errors:
            note += "\n部分检索未获取：" + "；".join(errors)
        return Section("论文", not errors, note)
    section = render_papers(papers, summaries, errors)
    return Section(section.title, section.ok, "今日没有新论文。\n" + section.body)


def snapshot_paper(paper: dict) -> dict:
    authors = paper.get("authors") or []
    return {
        "id": str(paper.get("id") or ""),
        "title": str(paper.get("title") or ""),
        "authors": [str(item) for item in authors if str(item).strip()] if isinstance(authors, list) else [],
        "published": str(paper.get("published") or ""),
        "link": str(paper.get("link") or ""),
        "query": str(paper.get("query") or ""),
    }


def render_papers(papers: list[dict], summaries: dict[str, str], errors: list[str]) -> Section:
    if not papers and errors:
        return Section("论文", False, "本次未获取：" + "；".join(errors))
    blocks: list[str] = []
    current_query = None
    number = 0
    for paper in papers:
        query = paper.get("query") or ""
        if query != current_query:
            current_query = query
            number = 0
            blocks.append(f"检索：{query}")
        if paper.get("empty"):
            blocks.append("没有条目。")
            continue
        number += 1
        blocks.append(_paper_block(number, paper, summaries.get(paper.get("id") or "")))
    if not blocks:
        blocks.append("没有条目。")
    if errors:
        blocks.append("部分检索未获取：" + "；".join(errors))
    return Section("论文", True, "\n".join(blocks))


def parse_feed(payload: bytes) -> list[dict]:
    root = ET.fromstring(payload)
    papers = []
    for entry in root.findall(f"{_ATOM}entry"):
        title = " ".join((entry.findtext(f"{_ATOM}title") or "").split())
        authors = [
            (author.findtext(f"{_ATOM}name") or "").strip()
            for author in entry.findall(f"{_ATOM}author")
        ]
        authors = [name for name in authors if name]
        published = (entry.findtext(f"{_ATOM}published") or "")[:10]
        entry_id = (entry.findtext(f"{_ATOM}id") or "").strip()
        link = _abstract_link(entry, entry_id)
        abstract = " ".join((entry.findtext(f"{_ATOM}summary") or "").split())
        papers.append(
            {
                "id": entry_id or link,
                "title": title or "无标题",
                "authors": authors,
                "published": published,
                "link": link,
                "abstract": abstract,
            }
        )
    return papers


def _paper_block(number: int, paper: dict, summary: str) -> str:
    authors = ", ".join(paper.get("authors") or []) or "未知"
    mark = "新" if paper.get("is_new") else "已见"
    title = to_plain_math(paper.get("title") or "") or "无标题"
    link = paper.get("link") or ""
    lines = [
        f"### {number}. {title}",
        f"**作者** {authors}",
        f"**日期** {paper.get('published') or ''}",
        f"**状态** {mark}",
    ]
    if link:
        lines.append(f"[摘要页]({link})")
    if summary:
        lines.extend(["**介绍**", to_plain_math(summary)])
    elif paper.get("is_new", True):
        abstract = to_plain_math(paper.get("abstract") or "")
        if abstract:
            lines.extend(["**摘要**", abstract])
    return "\n".join(lines)


def _abstract_link(entry: ET.Element, entry_id: str) -> str:
    for element in entry.findall(f"{_ATOM}link"):
        href = element.get("href") or ""
        if element.get("rel") == "alternate" and href and not href.endswith(".pdf"):
            return href
    if entry_id.endswith(".pdf"):
        return entry_id[: -len(".pdf")]
    return entry_id


def _query_url(query: str, max_results: int) -> str:
    params = urlencode(
        {
            "search_query": query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    return f"https://export.arxiv.org/api/query?{params}"
