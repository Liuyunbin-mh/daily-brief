"""Assembly, isolated collector failures, and one-send-per-day behavior."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from daily_brief.collectors.arxiv import collect_arxiv, parse_feed
from daily_brief.collectors.machine import collect_machine
from daily_brief.collectors.weather import WeatherResult, collect_weather
from daily_brief.config import load_config, write_coordinates
from daily_brief.mailer import MailError, read_secret
from daily_brief.report import Section, render
from daily_brief.state import load_state, save_state
from daily_brief.__main__ import execute

DAY = date(2026, 10, 15)


def _raise(exc: Exception):
    raise exc


ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1234.5678</id>
    <title>A  Sample   Title</title>
    <published>2026-10-14T00:00:00Z</published>
    <author><name>Ada</name></author>
    <author><name>Lin</name></author>
    <summary>A short abstract about the sample.</summary>
    <link title="pdf" href="http://arxiv.org/pdf/1234.5678" rel="related" type="application/pdf"/>
    <link rel="alternate" type="text/html" href="http://arxiv.org/abs/1234.5678"/>
  </entry>
</feed>
"""


def _write_config(directory: Path, *, latitude: str = "", longitude: str = "") -> Path:
    secret = directory / "secret"
    coords = ""
    if latitude:
        coords += f"latitude = {latitude}\n"
    if longitude:
        coords += f"longitude = {longitude}\n"
    path = directory / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[location]",
                'city = "Beijing"',
                'timezone = "auto"',
                coords.rstrip(),
                "",
                "[arxiv]",
                'queries = ["cat:cs.LG", "cat:stat.ML"]',
                "max_results = 5",
                "",
                "[email]",
                'smtp_host = "smtp.example.com"',
                "smtp_port = 465",
                'username = "you@example.com"',
                'from_addr = "you@example.com"',
                'to = ["you@example.com"]',
                f'secret_file = "{secret}"',
                "",
                "[delivery]",
                'channel = "email"',
                "",
                "[deepseek]",
                "enabled = false",
                "",
                "[paths]",
                f'report_dir = "{directory / "reports"}"',
                f'state_path = "{directory / "state.json"}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class RenderTests(unittest.TestCase):
    def test_failed_section_stays_beside_successful_ones(self):
        body = render(
            [
                Section("本机", True, "磁盘正常"),
                Section("天气", False, "本次未获取：超时"),
                Section("论文", True, "没有条目。"),
            ],
            DAY,
        )
        self.assertIn("## 本机", body)
        self.assertIn("本次未获取：超时", body)
        self.assertIn("## 论文", body)
        self.assertIn("# 早报 2026-10-15", body)


class CoordinateTests(unittest.TestCase):
    def test_write_coordinates_once(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            path = _write_config(Path(raw))
            write_coordinates(path, 39.9075, 116.39723)
            write_coordinates(path, 39.9075, 116.39723)
            loaded = load_config(path)
            self.assertAlmostEqual(loaded.latitude, 39.9075)
            self.assertAlmostEqual(loaded.longitude, 116.39723)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("latitude ="), 1)
            self.assertEqual(text.count("longitude ="), 1)

    def test_replaces_commented_coordinates(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            path = _write_config(Path(raw))
            text = path.read_text(encoding="utf-8").replace(
                'timezone = "auto"\n',
                'timezone = "auto"\n# latitude = 1\n# longitude = 2\n',
            )
            path.write_text(text, encoding="utf-8")
            write_coordinates(path, 31.2, 121.5)
            loaded = load_config(path)
            self.assertAlmostEqual(loaded.latitude, 31.2)
            self.assertAlmostEqual(loaded.longitude, 121.5)


class WeatherTests(unittest.TestCase):
    def test_cached_coordinates_skip_geocoding(self):
        calls = []

        def fetch_json(url):
            calls.append(url)
            if "air-quality" in url:
                return {"current": {"us_aqi": 42, "pm2_5": 10.2}}
            return {
                "current": {"temperature_2m": 18.2, "weather_code": 0, "wind_speed_10m": 3.1},
                "daily": {
                    "temperature_2m_max": [20.0],
                    "temperature_2m_min": [12.0],
                    "precipitation_sum": [0.0],
                },
            }

        result = collect_weather(
            city="Beijing",
            latitude=39.9,
            longitude=116.4,
            timezone="auto",
            fetch_json=fetch_json,
        )
        self.assertTrue(result.section.ok)
        self.assertIsNone(result.resolved)
        self.assertEqual(len(calls), 2)
        self.assertFalse(any("geocoding" in url for url in calls))
        self.assertIn("18.2°C", result.section.body)
        self.assertIn("US AQI 42", result.section.body)

    def test_geocode_failure_does_not_raise(self):
        result = collect_weather(
            city="",
            latitude=None,
            longitude=None,
            timezone="auto",
            fetch_json=lambda url: {},
        )
        self.assertFalse(result.section.ok)
        self.assertIn("本次未获取", result.section.body)
        self.assertIsInstance(result, WeatherResult)


class ArxivTests(unittest.TestCase):
    def test_parse_keeps_abstract_link(self):
        papers = parse_feed(ATOM)
        self.assertEqual(papers[0]["title"], "A Sample Title")
        self.assertEqual(papers[0]["authors"], ["Ada", "Lin"])
        self.assertEqual(papers[0]["link"], "http://arxiv.org/abs/1234.5678")
        self.assertEqual(papers[0]["abstract"], "A short abstract about the sample.")
        self.assertFalse(papers[0]["link"].endswith(".pdf"))

    def test_second_query_waits_and_first_query_failure_is_isolated(self):
        pauses = []

        def fetch_bytes(url):
            if "cs.LG" in url:
                raise TimeoutError("超时")
            return ATOM

        section, found, _records, _errors = collect_arxiv(
            queries=["cat:cs.LG", "cat:stat.ML", "extra", "dropped"],
            max_results=5,
            seen_ids=[],
            fetch_bytes=fetch_bytes,
            sleep=lambda seconds: pauses.append(seconds),
        )
        self.assertTrue(section.ok)
        self.assertIn("A Sample Title", section.body)
        self.assertIn("**状态** 新", section.body)
        self.assertIn("部分检索未获取", section.body)
        self.assertEqual(pauses, [3, 3])
        self.assertEqual(
            found,
            ["http://arxiv.org/abs/1234.5678", "http://arxiv.org/abs/1234.5678"],
        )
        self.assertNotIn("dropped", section.body)

    def test_unseen_papers_skip_seen_ids_and_duplicates(self):
        from daily_brief.collectors.arxiv import unseen_papers

        fresh, real = unseen_papers(
            [
                {"id": "old", "title": "Old"},
                {"id": "new", "title": "New"},
                {"id": "new", "title": "New again"},
                {"empty": True, "id": ""},
            ],
            ["old"],
        )
        self.assertTrue(real)
        self.assertEqual([paper["id"] for paper in fresh], ["new"])
        self.assertTrue(fresh[0]["is_new"])

    def test_seen_paper_without_cached_summary_omits_the_abstract(self):
        from daily_brief.collectors.arxiv import render_papers

        paper = {
            "query": "cat:cs.LG",
            "id": "http://arxiv.org/abs/1",
            "title": "Old Title",
            "authors": ["Ada"],
            "published": "2026-10-01",
            "link": "http://arxiv.org/abs/1",
            "abstract": "SECRET ABSTRACT TEXT",
            "is_new": False,
        }
        seen = render_papers([paper], {}, [])
        self.assertNotIn("SECRET ABSTRACT TEXT", seen.body)
        self.assertIn("**状态** 已见", seen.body)
        self.assertIn("[摘要页](http://arxiv.org/abs/1)", seen.body)
        fresh = render_papers([{**paper, "is_new": True}], {}, [])
        self.assertIn("**摘要**", fresh.body)
        self.assertIn("SECRET ABSTRACT TEXT", fresh.body)


class MachineTests(unittest.TestCase):
    def test_lists_only_large_files_in_the_top_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            downloads = Path(raw)
            small = downloads / "note.txt"
            small.write_text("hi", encoding="utf-8")
            large = downloads / "disk.img"
            large.write_bytes(b"x" * 150)
            nested = downloads / "subdir"
            nested.mkdir()
            (nested / "hidden.img").write_text("x" * 200, encoding="utf-8")

            class Result:
                returncode = 0
                stdout = "0 loaded units listed.\nfoo.service loaded failed failed Example\n"
                stderr = ""

            section = collect_machine(
                home=downloads,
                downloads=downloads,
                min_bytes=100,
                runner=lambda: Result(),
            )
            self.assertTrue(section.ok)
            self.assertIn("disk.img", section.body)
            self.assertNotIn("note.txt", section.body)
            self.assertNotIn("hidden.img", section.body)
            self.assertIn("foo.service", section.body)


class ExecuteTests(unittest.TestCase):
    def _config(self, directory: Path):
        return load_config(_write_config(directory))

    def _run(self, config, **kwargs):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = execute(config, **kwargs)
        return code, stderr.getvalue()

    def test_dry_run_writes_report_without_sending(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw))
            sent = []
            code, _stderr = self._run(
                config,
                dry_run=True,
                today=DAY,
                machine_fn=lambda: Section("本机", True, "磁盘正常"),
                weather_fn=lambda **kwargs: WeatherResult(Section("天气", False, "本次未获取：超时"), None),
                arxiv_fn=lambda **kwargs: (Section("论文", True, "没有条目。"), []),
                send_fn=lambda **kwargs: sent.append(kwargs),
            )
            self.assertEqual(code, 0)
            self.assertEqual(sent, [])
            report = config.report_dir / "2026-10-15.md"
            self.assertIn("本次未获取：超时", report.read_text(encoding="utf-8"))
            self.assertIn("没有条目。", report.read_text(encoding="utf-8"))
            self.assertEqual(load_state(config.state_path)["sent_on"], "")

    def test_already_sent_skips_work(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw))
            save_state(config.state_path, {"sent_on": "2026-10-15", "seen_arxiv_ids": []})
            called = []
            code, _stderr = self._run(
                config,
                dry_run=False,
                today=DAY,
                machine_fn=lambda: called.append("machine") or Section("本机", True, "x"),
                send_fn=lambda **kwargs: called.append("send"),
            )
            self.assertEqual(code, 0)
            self.assertEqual(called, [])
            self.assertFalse((config.report_dir / "2026-10-15.md").exists())

    def test_send_failure_keeps_report_and_returns_1(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw))
            code, _stderr = self._run(
                config,
                dry_run=False,
                today=DAY,
                machine_fn=lambda: Section("本机", True, "磁盘正常"),
                weather_fn=lambda **kwargs: WeatherResult(Section("天气", True, "晴"), None),
                arxiv_fn=lambda **kwargs: (Section("论文", True, "没有条目。"), ["http://arxiv.org/abs/1"]),
                read_secret_fn=lambda path: "auth-code",
                send_fn=lambda **kwargs: (_raise(MailError("发信失败：登录被拒绝"))),
            )
            self.assertEqual(code, 1)
            self.assertTrue((config.report_dir / "2026-10-15.md").is_file())
            self.assertEqual(load_state(config.state_path)["sent_on"], "")

    def test_success_marks_sent_and_seen_ids(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw))
            config.report_dir.mkdir()
            old = config.report_dir / "2026-10-01.md"
            kept = config.report_dir / "2026-10-02.md"
            old.write_text("old", encoding="utf-8")
            kept.write_text("kept", encoding="utf-8")
            (config.report_dir / "notes.txt").write_text("leave", encoding="utf-8")
            paper = {
                "id": "http://arxiv.org/abs/1",
                "title": "Fresh Title",
                "authors": ["Ada"],
                "published": "2026-10-15",
                "link": "http://arxiv.org/abs/1",
                "query": "cat:cs.LG",
                "abstract": "SECRET ABSTRACT",
            }
            code, _stderr = self._run(
                config,
                dry_run=False,
                today=DAY,
                machine_fn=lambda: Section("本机", True, "磁盘正常"),
                weather_fn=lambda **kwargs: WeatherResult(Section("天气", True, "晴"), None),
                arxiv_fn=lambda **kwargs: (Section("论文", True, "1. Title\n   状态：新"), [paper["id"]], [paper], []),
                read_secret_fn=lambda path: "auth-code",
                send_fn=lambda **kwargs: None,
            )
            self.assertEqual(code, 0)
            state = load_state(config.state_path)
            self.assertEqual(state["sent_on"], "2026-10-15")
            self.assertEqual(state["seen_arxiv_ids"], ["http://arxiv.org/abs/1"])
            self.assertEqual(state["last_batch"][0]["title"], "Fresh Title")
            self.assertNotIn("abstract", state["last_batch"][0])
            self.assertIn("Fresh Title", (config.report_dir / "2026-10-15.md").read_text(encoding="utf-8"))
            self.assertEqual(old.read_text(encoding="utf-8"), "old")
            self.assertEqual(kept.read_text(encoding="utf-8"), "kept")
            self.assertTrue((config.report_dir / "2026-10-15.md").is_file())
            self.assertEqual((config.report_dir / "notes.txt").read_text(encoding="utf-8"), "leave")

    def test_repeat_day_pushes_last_batch_without_deepseek(self):
        import tempfile
        from datetime import date

        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw))
            config.deepseek_enabled = True
            previous = {
                "id": "http://arxiv.org/abs/old",
                "title": "Old Title",
                "authors": ["Ada"],
                "published": "2026-10-14",
                "link": "http://arxiv.org/abs/old",
                "query": "cat:cs.LG",
            }
            incoming = {
                "id": "http://arxiv.org/abs/new",
                "title": "New Title",
                "authors": ["Lin"],
                "published": "2026-10-15",
                "link": "http://arxiv.org/abs/new",
                "query": "cat:cs.LG",
                "abstract": "OLDSECRET should stay out",
            }
            save_state(
                config.state_path,
                {
                    "sent_on": "",
                    "seen_arxiv_ids": [previous["id"]],
                    "summaries": {previous["id"]: "旧介绍"},
                    "last_batch": [previous],
                },
            )
            calls = []

            def summarize(papers, **kwargs):
                calls.append([paper["id"] for paper in papers])
                return {papers[0]["id"]: "新介绍"}

            def feed(**kwargs):
                return (Section("论文", True, "ignored"), [], [previous, incoming, incoming], [])

            code, _stderr = self._run(
                config,
                dry_run=False,
                today=DAY,
                machine_fn=lambda: Section("本机", True, "磁盘正常"),
                weather_fn=lambda **kwargs: WeatherResult(Section("天气", True, "晴"), None),
                arxiv_fn=feed,
                read_key_fn=lambda path: "key",
                summarize_fn=summarize,
                read_secret_fn=lambda path: "auth-code",
                send_fn=lambda **kwargs: None,
            )
            self.assertEqual(code, 0)
            report = (config.report_dir / "2026-10-15.md").read_text(encoding="utf-8")
            self.assertIn("New Title", report)
            self.assertNotIn("Old Title", report)
            self.assertNotIn("OLDSECRET", report)
            self.assertEqual(calls, [["http://arxiv.org/abs/new"]])
            state = load_state(config.state_path)
            self.assertEqual([item["id"] for item in state["last_batch"]], ["http://arxiv.org/abs/new"])
            calls.clear()
            code, _stderr = self._run(
                config,
                dry_run=False,
                today=date(2026, 10, 16),
                machine_fn=lambda: Section("本机", True, "磁盘正常"),
                weather_fn=lambda **kwargs: WeatherResult(Section("天气", True, "晴"), None),
                arxiv_fn=lambda **kwargs: (Section("论文", True, "ignored"), [], [incoming], []),
                read_key_fn=lambda path: (_ for _ in ()).throw(AssertionError("不应读取密钥")),
                summarize_fn=summarize,
                read_secret_fn=lambda path: "auth-code",
                send_fn=lambda **kwargs: None,
            )
            self.assertEqual(code, 0)
            self.assertEqual(calls, [])
            repeat = (config.report_dir / "2026-10-16.md").read_text(encoding="utf-8")
            self.assertIn("今日没有新论文。", repeat)
            self.assertIn("New Title", repeat)
            self.assertIn("新介绍", repeat)
            self.assertEqual(
                [item["id"] for item in load_state(config.state_path)["last_batch"]],
                ["http://arxiv.org/abs/new"],
            )

    def test_failed_send_and_dry_run_keep_older_reports(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw))
            config.report_dir.mkdir()
            old = config.report_dir / "2026-09-01.md"
            old.write_text("old", encoding="utf-8")
            code, _stderr = self._run(
                config,
                dry_run=True,
                today=DAY,
                machine_fn=lambda: Section("本机", True, "磁盘正常"),
                weather_fn=lambda **kwargs: WeatherResult(Section("天气", True, "晴"), None),
                arxiv_fn=lambda **kwargs: (Section("论文", True, "没有条目。"), []),
            )
            self.assertEqual(code, 0)
            self.assertTrue(old.is_file())
            code, _stderr = self._run(
                config,
                dry_run=False,
                today=DAY,
                machine_fn=lambda: Section("本机", True, "磁盘正常"),
                weather_fn=lambda **kwargs: WeatherResult(Section("天气", True, "晴"), None),
                arxiv_fn=lambda **kwargs: (Section("论文", True, "没有条目。"), []),
                read_secret_fn=lambda path: "auth-code",
                send_fn=lambda **kwargs: (_raise(MailError("发信失败：登录被拒绝"))),
            )
            self.assertEqual(code, 1)
            self.assertTrue(old.is_file())

    def test_missing_secret_does_not_open_smtp(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            config = self._config(Path(raw))
            with mock.patch("daily_brief.mailer.smtplib.SMTP_SSL") as smtp:
                code, errors = self._run(
                    config,
                    dry_run=False,
                    today=DAY,
                    machine_fn=lambda: Section("本机", True, "磁盘正常"),
                    weather_fn=lambda **kwargs: WeatherResult(Section("天气", True, "晴"), None),
                    arxiv_fn=lambda **kwargs: (Section("论文", True, "没有条目。"), []),
                )
            self.assertEqual(code, 1)
            smtp.assert_not_called()
            self.assertIn("本次不发信", errors)


class MathTextTests(unittest.TestCase):
    def test_common_formulas_become_readable(self):
        from daily_brief.mathtext import to_plain_math

        self.assertEqual(to_plain_math(r"up to $1.9\times$ and $1 - 1/e^2$"), "up to 1.9× and 1 - 1/e²")
        self.assertEqual(to_plain_math(r"the $\textit{price-of-anarchy}$"), "the price-of-anarchy")
        self.assertIn(r"\name", to_plain_math(r"We propose \name, which"))


class SummaryTests(unittest.TestCase):
    def test_summary_cache_keeps_newest_thirty_and_seen_ids_keep_four_hundred(self):
        from daily_brief.state import SEEN_LIMIT, SUMMARY_LIMIT, remember_ids, remember_summaries

        ids = [f"id-{index}" for index in range(SEEN_LIMIT + 5)]
        cached = {item: f"介绍 {item}" for item in ids}
        kept = remember_summaries(cached, {"id-new": "新介绍"}, ids + ["id-new"])
        self.assertEqual(len(kept), SUMMARY_LIMIT)
        self.assertEqual(list(kept)[-1], "id-new")
        self.assertNotIn(ids[0], kept)
        self.assertEqual(len(remember_ids([], ids)), SEEN_LIMIT)

    def test_roles_keep_full_abstracts_and_one_deep_read(self):
        from daily_brief.summarize import pending_papers, summarize_papers

        brief_abstract = "alpha beta gamma is the whole brief abstract"
        deep_sentence = "We propose a tiny method for speech"
        deep_abstract = deep_sentence + " " + ("background " * 80)
        papers = [
            {"id": "old", "title": "Old", "abstract": "already read", "query": "q", "is_new": False},
            {"id": "skip", "title": "Skip", "abstract": "SKIPME entirely", "query": "q", "is_new": True},
            {"id": "brief", "title": "Brief", "abstract": brief_abstract, "query": "q", "is_new": True},
            {
                "id": "http://arxiv.org/abs/2610.20606v1",
                "title": "Deep",
                "abstract": deep_abstract,
                "query": "q",
                "is_new": True,
            },
            {
                "id": "http://arxiv.org/abs/2610.11111v1",
                "title": "Second",
                "abstract": "SECOND_DEEP should be demoted",
                "query": "q",
                "is_new": True,
            },
        ]
        waiting = pending_papers(papers, {})
        self.assertEqual([item["id"] for item in waiting], ["skip", "brief", papers[3]["id"], papers[4]["id"]])
        calls = []
        fetches = []

        def post_json(url, payload, headers, timeout):
            calls.append((payload, timeout))
            system = payload["messages"][0]["content"]
            if "筛选员" in system:
                body = json.dumps(
                    {
                        "papers": [
                            {"index": 1, "relevance": "low", "depth": "deep", "reason": "不相关", "claims": [], "questions": []},
                            {"index": 2, "relevance": "medium", "depth": "brief", "reason": "相关", "claims": [], "questions": [1]},
                            {
                                "index": 3,
                                "relevance": "high",
                                "depth": "deep",
                                "reason": "最值得读",
                                "claims": [deep_sentence],
                                "questions": [1],
                            },
                            {
                                "index": 4,
                                "relevance": "medium",
                                "depth": "deep",
                                "reason": "次选",
                                "claims": [],
                                "questions": [1],
                            },
                        ]
                    }
                )
            elif "简要介绍" in system:
                body = json.dumps(
                    {
                        "papers": [
                            {
                                "index": 1,
                                "problem": "做了简要的事",
                                "method": "用了简要方法",
                                "result": "没有数字",
                                "relation": "和方向有关",
                                "quotes": {
                                    "problem": "alpha beta gamma",
                                    "method": "not in the abstract",
                                    "result": "alpha beta gamma",
                                    "relation": "alpha beta gamma",
                                },
                            },
                            {
                                "index": 2,
                                "problem": "次选问题",
                                "method": "次选方法",
                                "result": "次选结果",
                                "relation": "次选关系",
                                "quotes": {key: "SECOND_DEEP" for key in ("problem", "method", "result", "relation")},
                            },
                        ]
                    }
                )
            elif "精读撰写员" in system:
                body = json.dumps(
                    {
                        "claims": [{"text": "提出小方法", "quote": deep_sentence[:28]}],
                        "answers": [{"n": 1, "text": "要解决语音问题", "quote": deep_sentence[:28]}],
                    }
                )
            else:
                body = json.dumps({"drop": ["q1"], "missing": []})
            return 200, {"choices": [{"message": {"content": body}}]}

        def fetch_bytes(url):
            fetches.append(url)
            page = (
                "<html><body><h2>Introduction</h2><p>"
                + deep_abstract
                + "</p><h2>References</h2><p>SECRET REFERENCE SHOULD NOT APPEAR</p></body></html>"
            )
            return page.encode()

        found = summarize_papers(
            waiting,
            api_key="test-key",
            model="deepseek-flash",
            fetch_bytes=fetch_bytes,
            post_json=post_json,
        )
        systems = [item[0]["messages"][0]["content"] for item in calls]
        self.assertEqual(sum("筛选员" in system for system in systems), 1)
        self.assertEqual(sum("简要介绍" in system for system in systems), 1)
        self.assertEqual(sum("精读撰写员" in system for system in systems), 1)
        self.assertEqual(sum("核对员" in system for system in systems), 1)
        self.assertNotIn("test-key", json.dumps([item[0] for item in calls]))
        screen_user = calls[0][0]["messages"][1]["content"]
        self.assertIn(brief_abstract, screen_user)
        self.assertNotIn("already read", screen_user)
        brief_users = [item[0]["messages"][1]["content"] for item in calls if "简要介绍" in item[0]["messages"][0]["content"]]
        self.assertIn("SECOND_DEEP", brief_users[0])
        self.assertNotIn("SKIPME", brief_users[0])
        deep_call = next(item for item in calls if "精读撰写员" in item[0]["messages"][0]["content"])
        self.assertEqual(deep_call[0]["thinking"], {"type": "enabled"})
        self.assertEqual(deep_call[0]["reasoning_effort"], "low")
        self.assertEqual(deep_call[1], 45)
        self.assertNotIn("SECRET REFERENCE", deep_call[0]["messages"][1]["content"])
        self.assertNotIn("筛选时的推荐理由", deep_call[0]["messages"][1]["content"].split("正文：")[0])
        self.assertEqual(fetches, ["https://arxiv.org/html/2610.20606"])
        self.assertIn("不展开", found["skip"])
        self.assertIn("摘要未说明", found["brief"])
        self.assertNotIn("not in the abstract", found["brief"])
        self.assertIn("alpha beta gamma", found["brief"])
        deep = found["http://arxiv.org/abs/2610.20606v1"]
        self.assertIn("**依据** 全文", deep)
        self.assertIn("最值得读", deep)
        self.assertIn("正文未说明", deep)
        self.assertNotIn("要解决语音问题", deep)
        self.assertNotIn("未经核对", deep)
        self.assertIn("次选问题", found["http://arxiv.org/abs/2610.11111v1"])

    def test_screen_failure_falls_back_to_briefs_without_fulltext(self):
        from daily_brief.summarize import summarize_papers

        fetches = []

        def post_json(url, payload, headers, timeout):
            system = payload["messages"][0]["content"]
            if "筛选员" in system:
                return 500, {"message": "busy"}
            return 200, {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "papers": [
                                        {
                                            "index": 1,
                                            "problem": "问题句",
                                            "method": "方法句",
                                            "result": "结果句",
                                            "relation": "关系句",
                                            "quotes": {
                                                "problem": "quoted abstract",
                                                "method": "quoted abstract",
                                                "result": "quoted abstract",
                                                "relation": "quoted abstract",
                                            },
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            }

        found = summarize_papers(
            [{"id": "new", "title": "New", "abstract": "quoted abstract stays whole", "query": "q", "is_new": True}],
            api_key="test-key",
            model="deepseek-flash",
            fetch_bytes=lambda url: fetches.append(url),
            post_json=post_json,
        )
        self.assertEqual(fetches, [])
        self.assertIn("筛选未解析", found.notes[0])
        self.assertIn("问题句", found["new"])

    def test_unverified_deep_read_is_still_kept(self):
        from daily_brief.summarize import summarize_papers

        sentence = "quoted abstract stays whole"

        def post_json(url, payload, headers, timeout):
            system = payload["messages"][0]["content"]
            if "筛选员" in system:
                body = json.dumps(
                    {
                        "papers": [
                            {
                                "index": 1,
                                "relevance": "high",
                                "depth": "deep",
                                "reason": "值得",
                                "claims": [sentence],
                                "questions": [],
                            }
                        ]
                    }
                )
            elif "精读撰写员" in system:
                body = json.dumps({"claims": [{"text": "保留主张", "quote": sentence}], "answers": []})
            else:
                return 500, {"message": "check failed"}
            return 200, {"choices": [{"message": {"content": body}}]}

        found = summarize_papers(
            [{"id": "http://arxiv.org/abs/1234.5678v1", "title": "T", "abstract": sentence, "query": "q", "is_new": True}],
            api_key="test-key",
            model="deepseek-flash",
            fetch_bytes=lambda url: (_ for _ in ()).throw(TimeoutError("no html")),
            post_json=post_json,
        )
        self.assertIn("未经核对", found["http://arxiv.org/abs/1234.5678v1"])
        self.assertIn("仅摘要（全文页尚未生成）", found["http://arxiv.org/abs/1234.5678v1"])
        self.assertIn("保留主张", found["http://arxiv.org/abs/1234.5678v1"])


class FeishuTests(unittest.TestCase):
    def test_missing_webhook_explains_next_step(self):
        import tempfile

        from daily_brief.feishu import FeishuError, read_webhook

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "feishu-webhook"
            with self.assertRaises(FeishuError) as raised:
                read_webhook(path)
            self.assertIn("本次不发送", str(raised.exception))

    def test_signed_payload_uses_timestamp(self):
        from daily_brief.feishu import send_feishu

        sent = []

        def post_json(url, payload):
            sent.append((url, payload))
            return 200, {"code": 0, "msg": "success"}

        send_feishu(
            webhook="https://example.invalid/hook",
            text="# 早报 2026-10-15\n\n## 本机\n磁盘正常\n\n## 天气\n晴",
            sign_secret="sign-secret",
            post_json=post_json,
            now=1_700_000_000,
        )
        payload = sent[0][1]
        self.assertEqual(payload["timestamp"], "1700000000")
        self.assertTrue(payload["sign"])
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["schema"], "2.0")
        self.assertEqual(payload["card"]["header"]["title"]["content"], "早报 2026-10-15")
        contents = [element["content"] for element in payload["card"]["body"]["elements"]]
        self.assertEqual(contents, ["## 本机\n\n磁盘正常", "## 天气\n\n晴"])


class SecretMessageTests(unittest.TestCase):
    def test_missing_file_explains_next_step(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "secret"
            with self.assertRaises(MailError) as raised:
                read_secret(path)
            self.assertIn("本次不发信", str(raised.exception))
            self.assertIn(str(path), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
