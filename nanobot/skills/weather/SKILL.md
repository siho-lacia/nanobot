---
name: weather
description: Get current weather and forecasts (no API key required).
homepage: https://open-meteo.com/en/docs
metadata: {"nanobot":{"emoji":"🌤️","requires":{"bins":["curl"]}}}
---

# Weather

Two free services, no API keys needed.

## Open-Meteo (primary)

Free, no key, reliable JSON API:
```bash
curl -s --max-time 15 "https://api.open-meteo.com/v1/forecast?latitude=22.5&longitude=114.1&current_weather=true"
```

Returns JSON with:
- `temperature` (°C)
- `windspeed` (km/h)
- `winddirection` (degrees, 0=N, 90=E, 180=S, 270=W)
- `weathercode` (WMO code: 0=clear, 1-2=partly cloudy, 3=overcast, 45-48=fog, 51-67=rain, 71-77=snow, 80-99=showers/thunderstorms)
- `is_day` (1=day, 0=night)

### Finding Coordinates
Use geocoding API:
```bash
curl -s "https://geocoding-api.open-meteo.com/v1/search?name=Shenzhen&count=1"
```

Docs: https://open-meteo.com/en/docs

## wttr.in (fallback)

Human-readable output, but can be slow/unreliable:
```bash
curl -s --max-time 15 "wttr.in/London?format=3"
# Output: London: ⛅️ +8°C
```

Compact format:
```bash
curl -s "wttr.in/London?format=%l:+%c+%t+%h+%w"
# Output: London: ⛅️ +8°C 71% ↙5km/h
```

Full forecast:
```bash
curl -s "wttr.in/London?T"
```

Format codes: `%c` condition · `%t` temp · `%h` humidity · `%w` wind · `%l` location · `%m` moon

Tips:
- URL-encode spaces: `wttr.in/New+York`
- Airport codes: `wttr.in/JFK`
- Units: `?m` (metric) `?u` (USCS)
- Today only: `?1` · Current only: `?0`
- PNG: `curl -s "wttr.in/Berlin.png" -o /tmp/weather.png`

Docs: https://wttr.in/:help
