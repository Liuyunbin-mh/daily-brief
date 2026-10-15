"""Local status from a few small reads. No package-index refresh and no hashing."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from daily_brief.report import Section

MIN_DOWNLOAD_BYTES = 100 * 1024 * 1024
_LIST_LIMIT = 20


def collect_machine(
    home: Path | None = None,
    downloads: Path | None = None,
    min_bytes: int = MIN_DOWNLOAD_BYTES,
    runner=None,
) -> Section:
    home = home or Path.home()
    downloads = downloads if downloads is not None else home / "Downloads"
    try:
        lines = [
            _disk_line("磁盘 /", Path("/")),
            _disk_line("磁盘 家目录", home),
            _memory_line(),
            _failed_units(runner or _default_runner),
            _large_downloads(downloads, min_bytes),
        ]
        return Section("本机", True, "\n".join(lines))
    except Exception as exc:
        return Section("本机", False, f"本次未获取：{exc}")


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _disk_line(label: str, path: Path) -> str:
    usage = shutil.disk_usage(path)
    free_ratio = (usage.free / usage.total * 100) if usage.total else 0
    return (
        f"{label}：剩余 {format_bytes(usage.free)} / 共 {format_bytes(usage.total)}"
        f"（剩余 {free_ratio:.0f}%）"
    )


def _memory_line() -> str:
    info: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            info[key] = int(parts[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0))
    return f"内存：可用 {format_bytes(available)} / 共 {format_bytes(total)}"


def _default_runner():
    return subprocess.run(
        ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _failed_units(runner) -> str:
    try:
        result = runner()
    except Exception as exc:
        return f"失败的服务：本次未获取（{exc}）"
    stdout = result.stdout or ""
    if result.returncode != 0 and not stdout.strip():
        detail = (result.stderr or "").strip() or f"退出码 {result.returncode}"
        return f"失败的服务：本次未获取（{detail}）"
    names = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0].isdigit():
            continue
        unit = stripped.split()[0]
        if "." in unit:
            names.append(unit)
    if not names:
        return "失败的服务：无"
    return "失败的服务：" + ", ".join(names)


def _large_downloads(downloads: Path, min_bytes: int) -> str:
    if not downloads.is_dir():
        return "下载目录大文件：目录不存在"
    found: list[tuple[int, str]] = []
    try:
        entries = list(downloads.iterdir())
    except OSError as exc:
        return f"下载目录大文件：本次未获取（{exc}）"
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            size = entry.stat().st_size
        except OSError:
            continue
        if size >= min_bytes:
            found.append((size, entry.name))
    if not found:
        return "下载目录大文件：无"
    found.sort(reverse=True)
    shown = found[:_LIST_LIMIT]
    lines = [f"- {name}（{format_bytes(size)}）" for size, name in shown]
    if len(found) > _LIST_LIMIT:
        lines.append(f"- 另有 {len(found) - _LIST_LIMIT} 个")
    return "下载目录大文件：\n" + "\n".join(lines)
