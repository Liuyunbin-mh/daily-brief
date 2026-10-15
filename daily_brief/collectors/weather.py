"""Open-Meteo forecast and air quality. Coordinates are resolved once."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from daily_brief.report import Section

_WEATHER = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    95: "雷暴",
    96: "雷暴伴冰雹",
    99: "强雷暴伴冰雹",
}


@dataclass
class WeatherResult:
    section: Section
    resolved: tuple[float, float] | None


def collect_weather(
    *,
    city: str,
    latitude: float | None,
    longitude: float | None,
    timezone: str,
    fetch_json,
) -> WeatherResult:
    try:
        lat, lon, newly_resolved = _resolve(city, latitude, longitude, fetch_json)
        forecast = fetch_json(_forecast_url(lat, lon, timezone))
        place = city or f"{lat:.4f}, {lon:.4f}"
        lines = [f"地点：{place}", _forecast_line(forecast), _daily_line(forecast)]
        try:
            air = fetch_json(_air_url(lat, lon, timezone))
            lines.append(_air_line(air))
        except Exception as exc:
            lines.append(f"空气质量：本次未获取（{exc}）")
        resolved = (lat, lon) if newly_resolved else None
        return WeatherResult(Section("天气", True, "\n".join(lines)), resolved)
    except Exception as exc:
        return WeatherResult(Section("天气", False, f"本次未获取：{exc}"), None)


def _resolve(city: str, latitude: float | None, longitude: float | None, fetch_json):
    if latitude is not None and longitude is not None:
        return latitude, longitude, False
    if not city:
        raise RuntimeError("配置中未填写城市或经纬度")
    query = urlencode({"name": city, "count": 1, "language": "zh", "format": "json"})
    payload = fetch_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}")
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"未找到城市：{city}")
    return float(results[0]["latitude"]), float(results[0]["longitude"]), True


def _forecast_url(latitude: float, longitude: float, timezone: str) -> str:
    query = urlencode(
        {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": timezone,
            "forecast_days": 1,
        }
    )
    return f"https://api.open-meteo.com/v1/forecast?{query}"


def _air_url(latitude: float, longitude: float, timezone: str) -> str:
    query = urlencode(
        {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "current": "us_aqi,pm2_5",
            "timezone": timezone,
        }
    )
    return f"https://air-quality-api.open-meteo.com/v1/air-quality?{query}"


def _forecast_line(payload: dict) -> str:
    current = payload["current"]
    code = int(current["weather_code"])
    label = _WEATHER.get(code, f"代码 {code}")
    return (
        f"当前 {float(current['temperature_2m']):.1f}°C，{label}，"
        f"风速 {float(current['wind_speed_10m']):.1f} m/s"
    )


def _daily_line(payload: dict) -> str:
    daily = payload["daily"]
    return (
        f"今日 {float(daily['temperature_2m_min'][0]):.1f}–"
        f"{float(daily['temperature_2m_max'][0]):.1f}°C，"
        f"降水 {float(daily['precipitation_sum'][0]):.1f} mm"
    )


def _air_line(payload: dict) -> str:
    current = payload["current"]
    return f"空气质量：US AQI {float(current['us_aqi']):.0f}，PM2.5 {float(current['pm2_5']):.1f} μg/m³"
