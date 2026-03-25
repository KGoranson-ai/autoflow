# autoflow-engine

Human-like typing simulation for AI agents, browser automation, and desktop workflows.

## Why AutoFlow

Automation that types at fixed speed or pastes in one shot is easy to detect and often blocked. Real users vary speed, pause after punctuation, hesitate, and make occasional typos they correct. AutoFlow simulates that behavior so automated typing blends in and works where naive automation fails.

## Features

- **Configurable WPM** — Base typing speed (words per minute) with per-character delay variation.
- **Humanization levels** — Low / Medium / High control punctuation pauses, thinking pauses, and optional typo simulation.
- **Speed variation** — Random faster/slower bursts; optional thinking and punctuation pauses.
- **Typos and corrections** — Optional realistic typos (neighbor keys) with backspace and correct character.
- **Unicode normalization** — Smart quotes, dashes, bullets normalized to ASCII for reliable output.
- **Spreadsheet mode** — Type CSV-style data cell-by-cell with Tab/Enter navigation.
- **CLI and library** — Use from the command line or embed `TypingEngine` in your own code.

## Install

```bash
pip install autoflow-engine
```

## Quick start

```python
from autoflow_engine import TypingConfig, TypingEngine

config = TypingConfig(
    wpm=50,
    humanization_level=2,  # 1=Low, 2=Medium, 3=High
    speed_variation=True,
    thinking_pauses=True,
    punctuation_pauses=True,
    typos_enabled=True,
    countdown_seconds=5,
)
engine = TypingEngine(config)
engine.type_text("Hello, world. Focus the target field before the countdown finishes.")
```

Spreadsheet (e.g. after focusing cell A1):

```python
engine.type_spreadsheet([["Name", "Score"], ["Alice", "92"], ["Bob", "88"]])
```

## CLI

```bash
autoflow --text 'Your text here' --wpm 50 --human-level 2
```

Options: `--countdown`, `--no-speed-variation`, `--no-thinking`, `--no-punctuation`, `--no-typos`.

## Use cases

**AI agents and browser automation** — Drive forms and web apps via keyboard. Human-like timing and typos reduce detection and avoid bot measures that flag instant paste or perfectly regular input.

**Virtual assistants and data entry** — Replay scripts or type extracted text into legacy UIs, CRMs, or terminal apps that accept keyboard input only. Configurable WPM and pauses match operator speed.

**RPA and legacy system automation** — Type into green-screen, desktop, or thick-client UIs where clipboard or API access isn’t available. Use `type_text` for free-form content and `type_spreadsheet` for tabular data with cell-by-cell entry.

---

## Backend API (Flask)

This repository also includes a small **production-style REST API** for licensing, version checks, and future Stripe/database integration. It is intended to run on [Railway](https://railway.app) (or any host that supports `gunicorn`).

### Setup

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with real secrets (never commit .env)
```

Run locally:

```bash
export FLASK_DEBUG=1   # optional
export PORT=5001
python app.py
```

Or with Gunicorn (matches production):

```bash
gunicorn app:app --bind 127.0.0.1:5001
```

### Environment variables

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | PostgreSQL connection string (for future use) |
| `STRIPE_SECRET_KEY` | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| `SECRET_KEY` | Flask session / signing |
| `LICENSE_SALT` | Salt for license material (future validation) |
| `DOWNLOAD_URL` | URL returned by `GET /api/version` |
| `CORS_ORIGINS` | Comma-separated allowed origins (defaults include `https://autoflow.app` and local dev) |
| `PORT` | Listen port (set automatically on Railway) |

Dependencies use **`psycopg[binary]`** (Psycopg 3) so installs work on Python 3.11–3.13. If you standardize on Python 3.12 and prefer the older driver, you can swap that line in `requirements.txt` for `psycopg2-binary==2.9.9`.

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check. Response: `{"status": "ok"}` |
| `GET` | `/api/version` | App version and download URL. Response: `{"version": "3.0.0", "download_url": "..."}` |
| `POST` | `/api/validate-license` | Stub license check. JSON body: `{"license_key": "<string>"}`. Returns `valid` and `message` until real validation is implemented. |

Errors return JSON such as `{"error": "..."}` with appropriate HTTP status codes.
