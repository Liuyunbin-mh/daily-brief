"""Read config, build today's brief, send it once, and exit."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from daily_brief.collectors.arxiv import collect_arxiv, fallback_section, render_papers, snapshot_paper, unseen_papers
from daily_brief.collectors.machine import collect_machine
from daily_brief.collectors.weather import collect_weather
from daily_brief.config import Config, load_config, write_coordinates
from daily_brief.feishu import read_sign_secret, read_webhook, send_feishu
from daily_brief.http_util import fetch_bytes, fetch_json
from daily_brief.mailer import read_secret, send_email
from daily_brief.report import Section, subject_for, write_report, render
from daily_brief.state import load_state, remember_ids, remember_summaries, save_state
from daily_brief.summarize import pending_papers, read_api_key, summarize_papers


def execute(
    config: Config,
    *,
    dry_run: bool,
    today: date | None = None,
    fetch_json_fn=fetch_json,
    fetch_bytes_fn=fetch_bytes,
    sleep_fn=time.sleep,
    machine_fn=collect_machine,
    weather_fn=collect_weather,
    arxiv_fn=collect_arxiv,
    read_secret_fn=read_secret,
    send_fn=send_email,
    read_webhook_fn=read_webhook,
    read_sign_fn=read_sign_secret,
    send_feishu_fn=send_feishu,
    read_key_fn=read_api_key,
    summarize_fn=summarize_papers,
) -> int:
    day = today or date.today()
    state = load_state(config.state_path)
    if not dry_run and state.get("sent_on") == day.isoformat():
        print(f"今日早报已发送（{day.isoformat()}），退出。")
        return 0

    machine = machine_fn()
    weather = weather_fn(
        city=config.city,
        latitude=config.latitude,
        longitude=config.longitude,
        timezone=config.timezone,
        fetch_json=fetch_json_fn,
    )
    if weather.resolved is not None:
        write_coordinates(config.config_path, weather.resolved[0], weather.resolved[1])
        config.latitude, config.longitude = weather.resolved
    cached = dict(state.get("summaries") or {})
    seen_ids = list(state.get("seen_arxiv_ids") or [])
    paper_section, _found_ids, records, errors = _arxiv_result(
        arxiv_fn(
            queries=config.queries,
            max_results=config.max_results,
            seen_ids=seen_ids,
            fetch_bytes=fetch_bytes_fn,
            sleep=sleep_fn,
            summaries=cached,
        )
    )
    fresh, had_papers = unseen_papers(records, seen_ids)
    if had_papers and not fresh:
        paper_section = fallback_section(list(state.get("last_batch") or []), cached, errors)
        summary_records: list[dict] = []
    elif fresh:
        paper_section = render_papers(fresh, cached, errors)
        summary_records = fresh
    else:
        summary_records = []
    paper_section, new_summaries = _fill_summaries(
        config,
        paper_section,
        summary_records,
        errors,
        cached,
        dry_run=dry_run,
        read_key_fn=read_key_fn,
        summarize_fn=summarize_fn,
        fetch_bytes_fn=fetch_bytes_fn,
    )
    pushed_ids = [paper["id"] for paper in fresh]
    if new_summaries:
        state["summaries"] = remember_summaries(
            cached,
            new_summaries,
            remember_ids(seen_ids, pushed_ids),
            config.summary_keep,
        )
        save_state(config.state_path, state)
    body = render([machine, weather.section, paper_section], day)
    print(body, end="", flush=True)
    report_path = write_report(config.report_dir, day, body)
    print(f"本地副本：{report_path}", flush=True)

    if dry_run:
        print("dry-run：未发送，未记录已发送，未调用 DeepSeek。")
        return 0

    try:
        destination = _deliver(
            config,
            body,
            day,
            read_secret_fn=read_secret_fn,
            send_fn=send_fn,
            read_webhook_fn=read_webhook_fn,
            read_sign_fn=read_sign_fn,
            send_feishu_fn=send_feishu_fn,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    state["sent_on"] = day.isoformat()
    if fresh:
        state["last_batch"] = [snapshot_paper(paper) for paper in fresh]
        state["seen_arxiv_ids"] = remember_ids(seen_ids, pushed_ids)
    state["summaries"] = remember_summaries(
        dict(state.get("summaries") or {}),
        new_summaries,
        state["seen_arxiv_ids"],
        config.summary_keep,
    )
    save_state(config.state_path, state)
    print(destination)
    return 0


def _arxiv_result(result) -> tuple[Section, list[str], list[dict], list[str]]:
    section = result[0]
    found_ids = list(result[1])
    records = list(result[2]) if len(result) > 2 else []
    errors = list(result[3]) if len(result) > 3 else []
    return section, found_ids, records, errors


def _fill_summaries(
    config: Config,
    section: Section,
    records: list[dict],
    errors: list[str],
    cached: dict[str, str],
    *,
    dry_run: bool,
    read_key_fn,
    summarize_fn,
    fetch_bytes_fn,
) -> tuple[Section, dict[str, str]]:
    if not config.deepseek_enabled:
        return section, {}
    waiting = pending_papers(records, cached)
    if not waiting:
        return section, {}
    if dry_run:
        return _with_note(section, "未调用 DeepSeek：本次不生成介绍。"), {}
    try:
        api_key = read_key_fn(config.deepseek_key_file)
        fresh = summarize_fn(
            waiting,
            api_key=api_key,
            model=config.deepseek_model,
            questions=config.deepseek_questions,
            fetch_bytes=fetch_bytes_fn,
        )
    except Exception as exc:
        return _with_note(section, str(exc)), {}
    merged = {**cached, **fresh}
    rendered = render_papers(records, merged, errors)
    for note in getattr(fresh, "notes", ()):
        rendered = _with_note(rendered, note)
    return rendered, dict(fresh)


def _with_note(section: Section, note: str) -> Section:
    body = section.body.rstrip() + "\n" + note
    return Section(section.title, section.ok, body)


def _deliver(
    config: Config,
    body: str,
    day: date,
    *,
    read_secret_fn,
    send_fn,
    read_webhook_fn,
    read_sign_fn,
    send_feishu_fn,
) -> str:
    if config.channel == "feishu":
        send_feishu_fn(
            webhook=read_webhook_fn(config.feishu_webhook_file),
            text=body,
            sign_secret=read_sign_fn(config.feishu_sign_file),
        )
        return "已发送至飞书"
    if config.channel != "email":
        raise RuntimeError(f"未知投递方式：{config.channel}。请使用 feishu 或 email。")
    send_fn(
        host=config.smtp_host,
        port=config.smtp_port,
        username=config.username,
        password=read_secret_fn(config.secret_file),
        from_addr=config.from_addr,
        to=config.to,
        subject=subject_for(day),
        body=body,
    )
    return f"已发送至 {', '.join(config.to)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="采集每日早报，发信后退出。")
    parser.add_argument("--config", type=Path, help="配置文件路径，默认 ~/.config/daily-brief/config.toml")
    parser.add_argument("--dry-run", action="store_true", help="打印并保存早报，不发信，不记录已发送")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return execute(config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
