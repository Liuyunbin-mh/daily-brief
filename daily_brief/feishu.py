"""Post one text message to a Feishu custom bot, then close the connection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

_PLACEHOLDERS = {"", "https://open.feishu.cn/open-apis/bot/v2/hook/your-token", "PASTE_FEISHU_WEBHOOK"}
_MARKDOWN_LIMIT = 10000


class FeishuError(Exception):
    """The brief was saved, but it was not delivered."""


def read_webhook(path: Path) -> str:
    if not path.is_file():
        raise FeishuError(
            f"未找到飞书 Webhook：{path}\n"
            "在飞书群里添加自定义机器人，把完整 Webhook 地址写入该文件，并执行 chmod 600。本次不发送。"
        )
    webhook = path.read_text(encoding="utf-8").strip()
    if webhook in _PLACEHOLDERS or webhook.startswith("#") or not webhook.startswith("https://"):
        raise FeishuError(
            f"飞书 Webhook 尚未填写：{path}\n"
            "请写入以 https:// 开头的完整地址。本次不发送。"
        )
    return webhook


def read_sign_secret(path: Path) -> str | None:
    """Return the bot sign secret when the file exists. Unsigned bots leave it absent."""
    if not path.is_file():
        return None
    secret = path.read_text(encoding="utf-8").strip()
    if not secret or secret.startswith("#") or secret == "PASTE_FEISHU_SIGN_SECRET":
        return None
    return secret


def card_from_markdown(text: str) -> dict:
    """Build a Card JSON 2.0 payload. Heading syntax only renders in that schema."""
    title, body = _split_title(text)
    elements = [{"tag": "markdown", "content": part} for part in _markdown_parts(body)]
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "blue",
        },
        "body": {"elements": elements},
    }


def _split_title(text: str) -> tuple[str, str]:
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip() or "每日早报", "\n".join(lines[1:]).strip()
    return "每日早报", text.strip()


def _markdown_parts(body: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.startswith("## ") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        chunk = "\n".join(current).strip()
        if chunk:
            sections.append(chunk)
    if not sections:
        sections = [body.strip() or "（空）"]
    fitted: list[str] = []
    for section in sections:
        for piece in _split_numbered(section):
            fitted.extend(_fit(_hard_breaks(piece)))
    return fitted


def _split_numbered(section: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in section.splitlines():
        if re.match(r"^(?:### )?\d+\. ", line) and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks or [section]


def _hard_breaks(text: str) -> str:
    """Feishu treats a single newline as a soft break and may hide it."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n\n".join(lines)


def _fit(section: str) -> list[str]:
    if len(section) <= _MARKDOWN_LIMIT:
        return [section]
    pieces = []
    remaining = section
    while remaining:
        pieces.append(remaining[:_MARKDOWN_LIMIT].rstrip())
        remaining = remaining[_MARKDOWN_LIMIT:].lstrip()
    return pieces


def sign_payload(timestamp: str, secret: str) -> str:
    """Feishu custom-bot signature: HMAC-SHA256 of an empty message, key is timestamp plus secret."""
    key = f"{timestamp}\n{secret}".encode()
    digest = hmac.new(key, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def send_feishu(
    *,
    webhook: str,
    text: str,
    sign_secret: str | None = None,
    post_json=None,
    now: float | None = None,
) -> None:
    payload: dict = {"msg_type": "interactive", "card": card_from_markdown(text)}
    if sign_secret:
        timestamp = str(int(now if now is not None else time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = sign_payload(timestamp, sign_secret)
    status, data = (post_json or _post_json)(webhook, payload)
    code = data.get("code", data.get("StatusCode"))
    if status >= 400 or code not in (0, "0", None):
        detail = data.get("msg") or data.get("StatusMessage") or status
        raise FeishuError(f"飞书发送失败：{detail}")


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    raw = json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "daily-brief/0.1"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
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
    return data if isinstance(data, dict) else {"msg": "响应不是 JSON 对象"}
