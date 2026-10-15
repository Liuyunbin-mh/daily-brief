"""Screen, write, and check new papers. Cached text is never sent again."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from daily_brief.fulltext import load_source

_PLACEHOLDERS = {"", "PASTE_DEEPSEEK_API_KEY", "your-deepseek-api-key"}
_API_URL = "https://api.deepseek.com/chat/completions"
_REASON_QUESTION = "筛选时的推荐理由"
DEFAULT_QUESTIONS = [
    "要解决的问题",
    "点名的已有不足",
    "核心做法",
    "关键假设",
    "主要结果（保留数字）",
    "比较对象",
    "作者承认的限制",
    "和本次检索方向的关系",
    "筛选时的推荐理由",
    "正文仍未说清的点",
]
_BRIEF_FIELDS = (
    ("问题", "problem"),
    ("方法", "method"),
    ("结果", "result"),
    ("和检索方向的关系", "relation"),
)
_TIMEOUTS = {"screen": 20, "brief": 20, "deep": 45, "verify": 20}
_RELEVANCE = {"high": 3, "medium": 2, "low": 1}


class SummaryError(Exception):
    """The brief can still be delivered; this paper batch has no new introduction."""


class Summaries(dict):
    def __init__(self, items: dict[str, str] | None = None, notes: list[str] | None = None):
        super().__init__(items or {})
        self.notes = list(notes or [])


def read_api_key(path: Path) -> str:
    if not path.is_file():
        raise SummaryError(
            f"未配置 DeepSeek，本次没有介绍。请把 API Key 写入 {path}，并执行 chmod 600。"
        )
    key = path.read_text(encoding="utf-8").strip()
    if key in _PLACEHOLDERS or key.startswith("#"):
        raise SummaryError(f"DeepSeek API Key 尚未填写：{path}。本次没有介绍。")
    return key


def pending_papers(papers: list[dict], cached: dict[str, str]) -> list[dict]:
    return [paper for paper in papers if paper.get("is_new") and paper.get("id") not in cached]


def summarize_papers(
    papers: list[dict],
    *,
    api_key: str,
    model: str,
    questions: list[str] | None = None,
    fetch_bytes=None,
    post_json=None,
) -> Summaries:
    if not papers:
        return Summaries()
    questions = [item for item in (questions or DEFAULT_QUESTIONS) if item.strip()] or list(DEFAULT_QUESTIONS)
    post = post_json or _post_json
    notes: list[str] = []
    try:
        screens = _screen(papers, questions, api_key=api_key, model=model, post=post)
    except SummaryError:
        screens = None
        notes.append("筛选未解析，本次全部按简要介绍。")
    skips, briefs, deep = _assign(screens, len(papers))
    found: dict[str, str] = {}
    for index in skips:
        screen = screens[index] if screens else {}
        reason = str(screen.get("reason") or "相关度低")
        found[papers[index]["id"]] = f"**取舍** 相关度低，不展开。\n理由：{reason}"
    if briefs:
        try:
            found.update(_write_briefs(papers, briefs, api_key=api_key, model=model, post=post))
        except SummaryError:
            if deep is None and not found:
                raise
    if deep is not None:
        _fill_deep(
            found,
            papers[deep],
            screens[deep] if screens else {},
            questions,
            api_key=api_key,
            model=model,
            fetch_bytes=fetch_bytes,
            post=post,
        )
    if not found:
        raise SummaryError("介绍未生成：模型输出无法按论文拆开")
    return Summaries(found, notes)


def _assign(screens: list[dict] | None, count: int) -> tuple[list[int], list[int], int | None]:
    if screens is None:
        return [], list(range(count)), None
    skips: list[int] = []
    briefs: list[int] = []
    deeps: list[tuple[int, int, int]] = []
    for index, screen in enumerate(screens):
        relevance = str(screen.get("relevance") or "medium")
        depth = str(screen.get("depth") or "brief")
        if relevance == "low" or depth == "skip":
            skips.append(index)
        elif depth == "deep":
            deeps.append((_RELEVANCE.get(relevance, 2), -index, index))
        else:
            briefs.append(index)
    deep = None
    if deeps:
        deeps.sort(reverse=True)
        deep = deeps[0][2]
        briefs.extend(item[2] for item in deeps[1:])
    return skips, briefs, deep


def _fill_deep(found, paper, screen, questions, *, api_key, model, fetch_bytes, post) -> None:
    source, basis = load_source(paper, fetch_bytes)
    missing = "正文未说明" if basis.startswith("全文") else "摘要未说明"
    try:
        lines = _draft_deep(paper, screen, questions, source, missing, api_key=api_key, model=model, post=post)
    except SummaryError:
        try:
            found.update(_write_briefs([paper], [0], api_key=api_key, model=model, post=post))
        except SummaryError:
            return
        return
    verified = True
    try:
        verdict = _verify(source, lines, screen, api_key=api_key, model=model, post=post)
        lines = _apply_verdict(lines, verdict, source, missing, list(screen.get("claims") or []))
    except SummaryError:
        verified = False
    found[paper["id"]] = _format_deep(lines, basis, verified)


def _screen(papers, questions, *, api_key, model, post) -> list[dict]:
    numbered = "\n".join(f"{index}. {item}" for index, item in enumerate(questions, start=1))
    user = f"问题列表：\n{numbered}\n\n" + _paper_blocks(papers)
    data = _complete(
        post,
        api_key=api_key,
        model=model,
        system=(
            "你是早报的筛选员。只根据摘要判断，不要补充摘要以外的信息。"
            "relevance 取 high、medium 或 low；depth 取 skip、brief 或 deep。"
            "低相关必须是 skip。最多一篇 deep，只标最值得精读的那篇。"
            "reason 用一句话。claims 只收录摘要里的原句。questions 是有依据的问题序号。"
            '只输出 JSON：{"papers":[{"index":1,"relevance":"medium","depth":"brief",'
            '"reason":"...","claims":["..."],"questions":[1]}]}'
        ),
        user=user,
        max_tokens=min(1600, 400 + 180 * len(papers)),
        thinking="off",
        timeout=_TIMEOUTS["screen"],
    )
    rows = _object(data).get("papers")
    if not isinstance(rows, list):
        raise SummaryError("介绍未生成：筛选结果无法解析")
    screens = [_blank_screen() for _ in papers]
    for fallback, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index") or fallback + 1) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(papers):
            continue
        claims = row.get("claims") if isinstance(row.get("claims"), list) else []
        question_numbers = []
        for item in row.get("questions") or []:
            try:
                question_numbers.append(int(item))
            except (TypeError, ValueError):
                continue
        screens[index] = {
            "relevance": str(row.get("relevance") or "medium").lower(),
            "depth": str(row.get("depth") or "brief").lower(),
            "reason": " ".join(str(row.get("reason") or "").split()),
            "claims": [" ".join(str(item).split()) for item in claims if str(item).strip()],
            "questions": question_numbers,
        }
    return screens


def _write_briefs(papers, indexes, *, api_key, model, post) -> dict[str, str]:
    selected = [papers[index] for index in indexes]
    data = _complete(
        post,
        api_key=api_key,
        model=model,
        system=(
            "你是早报的撰写员。只根据摘要写简要介绍，不要编造数字或文献。"
            "每项一句话，quotes 里放不超过30字的摘要连续原句。摘要没有的内容写「摘要未说明」，对应 quote 留空。"
            '只输出 JSON：{"papers":[{"index":1,"problem":"...","method":"...","result":"...","relation":"...",'
            '"quotes":{"problem":"...","method":"...","result":"...","relation":"..."}}]}'
        ),
        user=_paper_blocks(selected),
        max_tokens=min(2000, 400 + 220 * len(selected)),
        thinking="off",
        timeout=_TIMEOUTS["brief"],
    )
    rows = _object(data).get("papers")
    if not isinstance(rows, list):
        raise SummaryError("介绍未生成：简要介绍无法解析")
    found = {}
    for fallback, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            slot = int(row.get("index") or fallback + 1) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= slot < len(selected):
            continue
        paper = selected[slot]
        if paper.get("id"):
            found[paper["id"]] = _format_brief(row, paper.get("abstract") or "")
    if not found:
        raise SummaryError("介绍未生成：简要介绍无法按论文拆开")
    return found


def _draft_deep(paper, screen, questions, source, missing, *, api_key, model, post) -> list[dict]:
    supported = {int(item) for item in screen.get("questions") or []}
    asked = [
        (number, question)
        for number, question in enumerate(questions, start=1)
        if question != _REASON_QUESTION and number in supported
    ]
    claims = [item for item in screen.get("claims") or [] if item]
    question_lines = "\n".join(f"{number}. {question}" for number, question in asked) or "（没有需要回答的问题）"
    claim_lines = "\n".join(f"{index}. {claim}" for index, claim in enumerate(claims)) or "（无）"
    user = (
        f"筛选出的主张：\n{claim_lines}\n\n要回答的问题：\n{question_lines}\n\n"
        f"标题：{paper.get('title') or '无标题'}\n正文：{source}"
    )
    data = _complete(
        post,
        api_key=api_key,
        model=model,
        system=(
            "你是早报的精读撰写员。只根据正文，不要编造文献、作者或数字。"
            "每条主张和每个问题用一两句，quote 必须是正文中的连续原句，不超过40字。"
            "正文没有依据时 text 写「未说明」，quote 留空。"
            '只输出 JSON：{"claims":[{"text":"...","quote":"..."}],"answers":[{"n":1,"text":"...","quote":"..."}]}'
        ),
        user=user,
        max_tokens=1500,
        thinking="low",
        timeout=_TIMEOUTS["deep"],
    )
    payload = _object(data)
    lines = []
    claim_rows = payload.get("claims") if isinstance(payload.get("claims"), list) else []
    for index, claim in enumerate(claims):
        row = claim_rows[index] if index < len(claim_rows) and isinstance(claim_rows[index], dict) else {}
        text, quote = _pair(row, source, 40, missing)
        grounded = _ground_quote(claim, source, 40)
        if text == missing and grounded:
            text, quote = claim, grounded
        lines.append({"key": f"c{index}", "kind": "claim", "label": "主张", "text": text or missing, "quote": quote})
    answer_rows = {}
    for row in payload.get("answers") or []:
        if isinstance(row, dict):
            try:
                answer_rows[int(row.get("n"))] = row
            except (TypeError, ValueError):
                continue
    for number, question in enumerate(questions, start=1):
        if question == _REASON_QUESTION:
            lines.append(
                {
                    "key": "reason",
                    "kind": "reason",
                    "label": question,
                    "text": screen.get("reason") or missing,
                    "quote": "",
                }
            )
            continue
        if number not in supported:
            lines.append({"key": f"q{number}", "kind": "answer", "label": question, "text": missing, "quote": ""})
            continue
        text, quote = _pair(answer_rows.get(number) or {}, source, 40, missing)
        lines.append({"key": f"q{number}", "kind": "answer", "label": question, "text": text or missing, "quote": quote})
    return lines


def _verify(source, lines, screen, *, api_key, model, post) -> dict:
    visible = [
        {"id": line["key"], "text": line["text"], "quote": line["quote"]}
        for line in lines
        if line["kind"] != "reason"
    ]
    claims = list(screen.get("claims") or [])
    user = (
        "撰写稿：\n"
        + json.dumps(visible, ensure_ascii=False)
        + "\n\n筛选主张：\n"
        + json.dumps(claims, ensure_ascii=False)
        + f"\n\n正文：{source}"
    )
    data = _complete(
        post,
        api_key=api_key,
        model=model,
        system=(
            "你是早报的核对员。对照正文检查撰写稿，不要重写。"
            "drop 填写没有正文依据的条目 id。missing 填写撰写稿没覆盖的筛选主张序号，从 0 开始。"
            '只输出 JSON：{"drop":[],"missing":[]}'
        ),
        user=user,
        max_tokens=600,
        thinking="off",
        timeout=_TIMEOUTS["verify"],
    )
    payload = _object(data)
    drop = [str(item) for item in payload.get("drop") or [] if str(item).strip()]
    missing = []
    for item in payload.get("missing") or []:
        try:
            missing.append(int(item))
        except (TypeError, ValueError):
            continue
    return {"drop": drop, "missing": missing}


def _apply_verdict(lines, verdict, source, missing, claims) -> list[dict]:
    dropped = set(verdict.get("drop") or [])
    updated = []
    covered = set()
    for line in lines:
        if line["key"] in dropped and line["kind"] != "reason":
            updated.append({**line, "text": missing, "quote": ""})
        else:
            updated.append(line)
        if line["kind"] == "claim":
            covered.add(line["text"])
    for index in verdict.get("missing") or []:
        if not 0 <= index < len(claims):
            continue
        quote = _ground_quote(claims[index], source, 40)
        if not quote or claims[index] in covered:
            continue
        updated.append({"key": f"c{index}", "kind": "claim", "label": "主张", "text": claims[index], "quote": quote})
    return updated


def _format_brief(row: dict, abstract: str) -> str:
    quotes = row.get("quotes") if isinstance(row.get("quotes"), dict) else {}
    lines = []
    for label, key in _BRIEF_FIELDS:
        text, quote = _pair({"text": row.get(key), "quote": quotes.get(key)}, abstract, 30, "摘要未说明")
        lines.append(f"**{label}** {text}" + (f"\n原句：{quote}" if quote else ""))
    return "\n".join(lines)


def _format_deep(lines: list[dict], basis: str, verified: bool) -> str:
    rendered = [f"**依据** {basis}"]
    for line in lines:
        rendered.append(f"**{line['label']}** {line['text']}")
        if line["quote"]:
            rendered.append(f"原句：{line['quote']}")
    if not verified:
        rendered.append("未经核对")
    return "\n".join(rendered)


def _pair(row: dict, source: str, limit: int, missing: str) -> tuple[str, str]:
    text = " ".join(str((row or {}).get("text") or "").split())
    quote = _ground_quote(str((row or {}).get("quote") or ""), source, limit)
    if text in {"", "未说明", "摘要未说明", "正文未说明"} or not quote:
        return missing, ""
    return text, quote


def _ground_quote(quote: str, source: str, limit: int) -> str:
    compact_quote = " ".join((quote or "").split())
    compact_source = " ".join((source or "").split())
    if not compact_quote or compact_quote not in compact_source:
        return ""
    return compact_quote[:limit]


def _paper_blocks(papers: list[dict]) -> str:
    blocks = []
    for index, paper in enumerate(papers, start=1):
        abstract = " ".join((paper.get("abstract") or "").split()) or "（无摘要）"
        blocks.append(
            f"[{index}]\n标题：{paper.get('title') or '无标题'}\n"
            f"检索：{paper.get('query') or ''}\n摘要：{abstract}"
        )
    return "\n\n".join(blocks)


def _blank_screen() -> dict:
    return {"relevance": "medium", "depth": "brief", "reason": "", "claims": [], "questions": []}


def _complete(post, *, api_key, model, system, user, max_tokens, thinking, timeout) -> str:
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if thinking == "low":
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = "low"
    else:
        payload["thinking"] = {"type": "disabled"}
    status, data = post(
        _API_URL,
        payload,
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout,
    )
    if status >= 400:
        detail = data.get("message") or data.get("msg") or status
        raise SummaryError(f"介绍未生成：{detail}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SummaryError("介绍未生成：响应里没有正文") from exc
    return str(content)


def _object(text: str) -> dict:
    raw = text.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise SummaryError("介绍未生成：响应不是 JSON")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SummaryError("介绍未生成：响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise SummaryError("介绍未生成：响应不是 JSON 对象")
    return data


def _post_json(url: str, payload: dict, headers: dict, timeout: float = 30) -> tuple[int, dict]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"User-Agent": "daily-brief/0.1", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, _decode(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _decode(exc.read())


def _decode(raw: bytes) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode())
    except json.JSONDecodeError:
        return {"msg": raw.decode(errors="replace")[:300]}
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return {"message": data["error"].get("message") or "请求失败"}
    return data if isinstance(data, dict) else {"msg": "响应不是 JSON 对象"}
