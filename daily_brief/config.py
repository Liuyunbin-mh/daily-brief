"""Load the local TOML config and write resolved coordinates back into it."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import tomllib

from daily_brief.summarize import DEFAULT_QUESTIONS

_MAX_QUERIES = 3
_MAX_RESULTS = 5
_SUMMARY_KEEP = 30
_SUMMARY_KEEP_MAX = 400
@dataclass
class Config:
    city: str
    latitude: float | None
    longitude: float | None
    timezone: str
    queries: list[str]
    max_results: int
    channel: str
    smtp_host: str
    smtp_port: int
    username: str
    from_addr: str
    to: list[str]
    secret_file: Path
    feishu_webhook_file: Path
    feishu_sign_file: Path
    deepseek_enabled: bool
    deepseek_model: str
    deepseek_key_file: Path
    deepseek_questions: list[str]
    deepseek_tokens_per_paper: int
    summary_keep: int
    config_path: Path
    report_dir: Path
    state_path: Path


def default_config_path() -> Path:
    base = Path(os.environ["XDG_CONFIG_HOME"]) if os.environ.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return base / "daily-brief" / "config.toml"


def default_report_dir() -> Path:
    base = Path(os.environ["XDG_DATA_HOME"]) if os.environ.get("XDG_DATA_HOME") else Path.home() / ".local" / "share"
    return base / "daily-brief" / "reports"


def default_state_path() -> Path:
    base = Path(os.environ["XDG_STATE_HOME"]) if os.environ.get("XDG_STATE_HOME") else Path.home() / ".local" / "state"
    return base / "daily-brief" / "state.json"


def default_secret_path() -> Path:
    return default_config_path().parent / "secret"


def default_feishu_webhook_path() -> Path:
    return default_config_path().parent / "feishu-webhook"


def default_feishu_sign_path() -> Path:
    return default_config_path().parent / "feishu-sign"


def default_deepseek_key_path() -> Path:
    return default_config_path().parent / "deepseek-key"


def load_config(path: Path | None = None) -> Config:
    config_path = path or default_config_path()
    if not config_path.is_file():
        raise FileNotFoundError(
            f"未找到配置文件：{config_path}。请把 config.example.toml 复制到该路径后再运行。"
        )
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    location = raw.get("location") or {}
    arxiv = raw.get("arxiv") or {}
    email = raw.get("email") or {}
    delivery = raw.get("delivery") or {}
    feishu = raw.get("feishu") or {}
    deepseek = raw.get("deepseek") or {}
    paths = raw.get("paths") or {}

    queries = [str(item).strip() for item in (arxiv.get("queries") or []) if str(item).strip()]
    max_results = int(arxiv.get("max_results", _MAX_RESULTS))
    secret_raw = str(email.get("secret_file") or "").strip()
    recipients = email.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]

    latitude = location.get("latitude")
    longitude = location.get("longitude")
    return Config(
        city=str(location.get("city") or "").strip(),
        latitude=float(latitude) if latitude is not None else None,
        longitude=float(longitude) if longitude is not None else None,
        timezone=str(location.get("timezone") or "auto"),
        queries=queries[:_MAX_QUERIES],
        max_results=max(1, min(max_results, _MAX_RESULTS)),
        channel=str(delivery.get("channel") or "email").strip().lower(),
        smtp_host=str(email.get("smtp_host") or "smtp.qq.com"),
        smtp_port=int(email.get("smtp_port") or 465),
        username=str(email.get("username") or ""),
        from_addr=str(email.get("from_addr") or email.get("username") or ""),
        to=[str(item) for item in recipients if str(item).strip()],
        secret_file=Path(secret_raw).expanduser() if secret_raw else default_secret_path(),
        feishu_webhook_file=_configured_path(feishu.get("webhook_file"), default_feishu_webhook_path()),
        feishu_sign_file=_configured_path(feishu.get("sign_file"), default_feishu_sign_path()),
        deepseek_enabled=_bool(deepseek.get("enabled", True)),
        deepseek_model=str(deepseek.get("model") or "deepseek-flash"),
        deepseek_key_file=_configured_path(deepseek.get("key_file"), default_deepseek_key_path()),
        deepseek_questions=_questions(deepseek.get("questions")),
        deepseek_tokens_per_paper=max(64, min(int(deepseek.get("max_tokens_per_paper", 120)), 200)),
        summary_keep=_bounded_int(deepseek.get("summary_keep"), _SUMMARY_KEEP, 1, _SUMMARY_KEEP_MAX),
        config_path=config_path,
        report_dir=_optional_path(paths.get("report_dir"), default_report_dir()),
        state_path=_optional_path(paths.get("state_path"), default_state_path()),
    )


def write_coordinates(path: Path, latitude: float, longitude: float) -> None:
    text = path.read_text(encoding="utf-8")
    text = _upsert_location_key(text, "latitude", f"{latitude:.6f}")
    text = _upsert_location_key(text, "longitude", f"{longitude:.6f}")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _optional_path(value: object, fallback: Path) -> Path:
    text = str(value or "").strip()
    return Path(text).expanduser() if text else fallback


def _configured_path(value: object, fallback: Path) -> Path:
    return _optional_path(value, fallback)


def _questions(value: object) -> list[str]:
    if not isinstance(value, list):
        return list(DEFAULT_QUESTIONS)
    items = [str(item).strip() for item in value if str(item).strip()]
    return items or list(DEFAULT_QUESTIONS)


def _bounded_int(value: object, default: int, low: int, high: int) -> int:
    if value is None or value == "":
        return default
    return max(low, min(int(value), high))


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _location_span(text: str) -> tuple[int, int] | None:
    match = re.search(r"(?m)^\[location\][ \t]*\n", text)
    if match is None:
        return None
    start = match.end()
    nxt = re.search(r"(?m)^\[", text[start:])
    end = start + nxt.start() if nxt else len(text)
    return start, end


def _upsert_location_key(text: str, key: str, value: str) -> str:
    line = f"{key} = {value}"
    span = _location_span(text)
    if span is None:
        suffix = "" if not text or text.endswith("\n") else "\n"
        return f"{text}{suffix}[location]\n{line}\n"
    start, end = span
    section = text[start:end]
    pattern = re.compile(rf"(?m)^[ \t]*#?[ \t]*{re.escape(key)}[ \t]*=[^\n]*\n?")
    if pattern.search(section):
        section = pattern.sub(line + "\n", section, count=1)
        return text[:start] + section + text[end:]
    return text[:start] + line + "\n" + text[start:]
