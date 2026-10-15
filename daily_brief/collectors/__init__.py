"""Independent collectors. A failure in one stays inside that section."""

from daily_brief.collectors.arxiv import collect_arxiv
from daily_brief.collectors.machine import collect_machine
from daily_brief.collectors.weather import collect_weather

__all__ = ["collect_arxiv", "collect_machine", "collect_weather"]
