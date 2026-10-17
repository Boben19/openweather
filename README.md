# DexTHER

> Whether you asked or not, Dex has the forecast.

Powered by Django and the OpenWeather API.

A responsive weather dashboard built with Django and the OpenWeather API: city search, browser geolocation (looked up by latitude/longitude), current conditions, a 24-hour strip, a 5-day forecast, °C/°F, recent places, and light/dark/system themes.

Developed by Jhon Grover Longsud and Allan Dexter P. Cuizon

## Setup

You need Python 3.10+ and a free OpenWeather API key (https://openweathermap.org/api). New keys can take a while to activate.

```bash
cd projectsite

# 1. Create and activate a virtual environment
python -m venv .venv
#   Windows (PowerShell):  .venv\Scripts\Activate.ps1
#   macOS / Linux:         source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment (never commit .env)
cp .env.example .env            # Windows: copy .env.example .env
#    then edit .env and set OPENWEATHER_API_KEY=your_key

# 4. Create the (small) Django database and run
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/.

Optional URL parameters: `/?city=Tokyo&units=imperial` or `/?lat=10.3157&lon=123.8854`.

## Configuration (`projectsite/.env`)

| Variable | Purpose | Default |
|---|---|---|
| `OPENWEATHER_API_KEY` | Your OpenWeather key (required) | none |
| `DJANGO_DEBUG` | `True` for local development | `False` |
| `DJANGO_SECRET_KEY` | Required when `DJANGO_DEBUG` is off | random per run when debugging |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts, needed when debug is off | localhost when debugging |
| `DJANGO_HTTPS` | Secure cookies and HTTP→HTTPS redirect | on whenever debug is off |
| `DEFAULT_CITY` | City on first load; empty shows the search prompt | `Puerto Princesa` |
| `WEATHER_CACHE_SECONDS` | Cache time for API results (0 disables) | `600` |

## Tests

```bash
cd projectsite
python manage.py check
python manage.py test
```

The automated tests **mock** OpenWeather (`requests.get` is patched), so they run offline without a key. They verify this app's behaviour and parsing, not OpenWeather's live service. To verify against the real API, run the app with a valid key and try a city search and "Use my location".

## How it works

- `weather/services.py`: validates input, calls the two documented endpoints (`/data/2.5/weather` and `/data/2.5/forecast`), parses and formats results, and caches successful responses.
- `weather/views.py`: one view (`/`) that renders the page. Search works as a normal GET form, and JavaScript upgrades it to load results without a full page reload.
- City searches call OpenWeather with `q=<city>`. The forecast then uses the coordinates OpenWeather resolved, so both panels describe the same place. Geolocation and recent places call both endpoints with `lat`/`lon`, never a city name.
- Times are shown in the searched place's own timezone (from OpenWeather's UTC offset). The hero is labelled **Observed** and the hourly and daily sections are labelled **Forecast**. "Updated" shows when the data was last fetched.
- Today's high/low in the forecast includes the observed temperature, because the forecast only covers upcoming hours.
- Weather icons are an inline SVG sprite and the font is the system font, so the page makes no third-party requests.

## Security notes

- Secrets live in `.env` (git-ignored). `.env.example` has no credentials.
- The API key is sent only server-side and is never logged or rendered.
- `DEBUG` defaults to off, and a `SECRET_KEY` is required outside debug mode.
- City names are validated (length, allowed characters). Coordinates are validated as plain numbers within range. All output is auto-escaped.
- For production, set `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY`, and `DJANGO_ALLOWED_HOSTS`, run `python manage.py collectstatic`, and serve `staticfiles/` from your web server. If you deploy behind a proxy that terminates HTTPS, also set `SECURE_PROXY_SSL_HEADER` in settings.

## Limitations

- Only the free-tier endpoints are used. Forecasts are 3-hourly, so "daily" values are derived from those intervals.
- City search uses OpenWeather's `q=` matching. Ambiguous names resolve to OpenWeather's top match; add a country code (`Paris, FR`) to be specific.
- Browser geolocation needs permission and a secure context (HTTPS or `localhost`).

## Credits

Weather data by [OpenWeather](https://openweathermap.org/).
