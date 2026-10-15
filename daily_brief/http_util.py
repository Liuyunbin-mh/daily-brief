"""Short HTTP reads. Callers set their own deadlines; nothing stays open."""

from __future__ import annotations

import json
import urllib.request

USER_AGENT = "daily-brief/0.1 (one-shot local digest; metadata only)"


def fetch_bytes(url: str, timeout: float = 10.0, accept: str = "*/*") -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str, timeout: float = 10.0) -> dict:
    payload = fetch_bytes(url, timeout=timeout, accept="application/json")
    data = json.loads(payload.decode())
    if not isinstance(data, dict):
        raise ValueError("响应不是 JSON 对象")
    return data
