# upload_to_meta_bot

**upload_to_meta_bot** is a Telegram bot for **publishing content to Facebook and Instagram** through the **Meta Graph API**. Admins configure pages, captions, media (photo, video, reels, stories), and optional **scheduled publishing** inside the bot’s job queue. The user interface supports **Arabic and English**. Optional integrations include **Google Drive** (archiving published videos) and **Supabase Storage** (public `image_url` for Instagram photo flows).

## Key Features

*   **Meta publishing**: Post to Facebook Page(s) and Instagram Business accounts linked to those pages, using a long-lived `META_ACCESS_TOKEN` and per-page tokens where required (e.g. Facebook photo stories).
*   **Rich media flows**: Photos, videos, Reels, and Stories—with **Instagram video preflight** (e.g. faststart / optional re-encode via **ffmpeg**) to reduce `ProcessingFailedError` from Meta.
*   **Scheduling**: Deferred publish jobs via **python-telegram-bot JobQueue** + **SQLAlchemy job store** (`data/jobs.sqlite3`); UTC offset is configurable under Meta settings for local `YYYY-MM-DD HH:MM` input.
*   **Administration**: Owner bootstrap on first run, granular **admin permissions**, user management, **broadcast**, and **ban/unban**.
*   **Google Drive**: Link folders and optionally archive video bytes after a successful publish.
*   **Operational hooks**: Optional Telegram channel for **publish result reports** (`PUBLISH_RESULTS_CHANNEL`), optional errors channel (`ERRORS_CHANNEL`), and error logging to `errors.txt`.
*   **Force-join**: Optional requirement for users to join configured chats before using the bot.
*   **Persistence**: Conversation and user data persisted under `data/persistence*`; main app data in **SQLite** via SQLAlchemy.

## Technology Stack & Architecture

*   **Runtime**: Python 3
*   **Telegram**: `python-telegram-bot` (polling, concurrent updates, HTML parse mode, Pickle persistence)
*   **Jobs**: `ptbcontrib` **PTBSQLAlchemyJobStore** for durable scheduled jobs
*   **Large media download**: **Telethon** (and Pyrogram fork) for fetching Telegram files above Bot API limits when publishing to Meta
*   **Database**: **SQLAlchemy** + SQLite (`DB_PATH`); WAL and pragmas tuned in `init_db`
*   **Migrations**: **Alembic** (see `alembic.ini` / `alembic/`)
*   **Meta**: Custom `meta/` package—Graph client, resumable upload (`rupload`), publishers, notifications, optional Supabase public URLs for Instagram images
*   **Google**: `google-api-python-client` + OAuth flow under `google_drive/`
*   **HTTP / async**: `aiohttp` for Graph and related calls
*   **Tests**: `pytest` — unit tests with mocks; optional live integration against Graph (see `docs/META_SETUP.md`)

## Project Structure

```
upload_to_meta_bot/
├── main.py                 # Entry point → setup_and_run()
├── MyApp.py                # ApplicationBuilder, persistence, job store
├── Config.py               # Environment-driven configuration
├── handlers.py             # Registers handlers and starts polling
├── start.py                # post_init (owner seed), /start, /admin
├── jobs.py                 # Scheduled Meta publish + Drive archive hooks
├── admin/                  # Admin modules (settings, Meta, Drive, broadcast, ban, …)
├── user/                   # User-facing handlers and settings
├── common/                 # Keyboards, decorators, force-join, errors
├── custom_filters/         # Admin, Owner, permissions, private chat, …
├── meta/                   # Graph client, publishers, video normalizer, …
├── google_drive/           # Drive service, credentials helper
├── models/                 # SQLAlchemy models and DB session helpers
├── tests/                  # unit/ + integration/
├── docs/
│   ├── META_SETUP.md       # Meta app, tokens, env vars, pytest (Arabic)
│   └── SYSTEMD_GUIDE.md    # Service deployment notes
├── alembic/                # DB migrations
├── data/                   # SQLite DBs, persistence (created at runtime)
└── requirements.txt        # Runtime dependencies
```

## Setup and Installation

1.  **Clone the repository** (or copy the project folder) and enter it:
    ```sh
    cd upload_to_meta_bot
    ```

2.  **Create a virtual environment** (recommended):
    ```sh
    python -m venv .venv
    .venv\Scripts\activate
    ```

3.  **Install dependencies:**
    ```sh
    pip install -r requirements.txt
    ```
    For development and tests:
    ```sh
    pip install -r requirements-dev.txt
    ```

4.  **Create a `.env` file** in the project root. Minimum variables (see `Config.py` for the full list):

    | Variable | Purpose |
    |----------|---------|
    | `BOT_TOKEN` | Telegram bot token |
    | `API_ID`, `API_HASH` | Telegram API credentials (Telethon / large media) |
    | `OWNER_ID` | Numeric Telegram user id of the owner |
    | `DB_PATH` | Path to SQLite file (e.g. `data/database.sqlite3`) |
    | `META_ACCESS_TOKEN` | Long-lived user/page-capable token for Graph |

    Common optional variables: `LOG_LEVEL`, `ERRORS_CHANNEL`, `PUBLISH_RESULTS_CHANNEL`, `META_GRAPH_VERSION`, `META_HTTP_TIMEOUT_TOTAL`, `TELEGRAM_MEDIA_MAX_MB`, `FFMPEG_BIN`, `IG_VIDEO_AUTOFIX_*`, `IG_VIDEO_REENCODE_IF_INCOMPATIBLE`, `IG_VIDEO_FORCE_REENCODE`, and Supabase keys for Instagram `image_url` workflows.

5.  **Meta and Instagram**: Follow **[docs/META_SETUP.md](docs/META_SETUP.md)** for app permissions, token setup, scheduling notes, and running tests (including optional live integration).

6.  **Google Drive** (optional): Place OAuth client credentials under `google_drive/credentials/` and use the project’s refresh-token helper; see `google_drive/credentials/README.md`.

7.  **Run the bot:**
    ```sh
    python main.py
    ```

8.  **Production / daemon**: See **[docs/SYSTEMD_GUIDE.md](docs/SYSTEMD_GUIDE.md)** for systemd-oriented deployment notes.

---

After the first successful start, the owner account is registered as admin in the database. Use `/admin` in a private chat (with admin rights) to open the admin panel.
