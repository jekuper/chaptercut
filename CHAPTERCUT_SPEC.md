# chaptercut — Project Specification

> Build spec for a ground-up rewrite of a personal Telegram bot that turns YouTube links into properly tagged MP3 tracks (splitting albums along chapter markers) or downloaded videos. This document is the single source of truth for the implementing agent. It describes **what** to build, **how** it is structured, **why** decisions were made, and **what went wrong in the predecessor project** so those mistakes are not repeated.
>
> Repository name: **`chaptercut`**. Python package name: `chaptercut`. CLI / container name: `chaptercut`.

---

## 0. TL;DR for the implementing agent

- Python 3.12+, **aiogram 3.x**, asyncio end to end, `uv` + `pyproject.toml`, `ruff`, `pyright`, `pytest`.
- Runs against a **self-hosted `telegram-bot-api` server in `--local` mode** (2 GB file limit, send files by path). There is **no third-party file-hosting fallback** in this project.
- Downloads via **yt-dlp**, cuts via **FFmpeg** (async subprocess), tags via **mutagen**, art via **Pillow**.
- Jobs go through a **persistent SQLite-backed queue** (survives restarts) processed by a single background worker with live progress messages.
- Processed videos are **cached on disk keyed by video ID**, written **atomically** so a crash can never leave a half-cache that poisons later requests.
- **Zero secrets in code**: `pydantic-settings` + `.env`; `.env.example` committed; `gitleaks` pre-commit hook; GitHub push protection on.
- Ship with Docker Compose (bot + bot-api server), a `justfile`, and a README.

---

## 1. Goals and non-goals

### Goals
1. A private, whitelisted Telegram bot: paste a YouTube link → receive either
   - **Audio**: one MP3 per chapter (or one MP3 for the whole video if no chapters), fully ID3-tagged with cover art, delivered as individual audio messages or a ZIP; or
   - **Video**: the video in a user-chosen quality.
2. Robust under restarts, concurrent requests, and large files.
3. Clean, typed, testable code that is a credible public portfolio piece.
4. Simple operation on a single small Linux VPS via Docker Compose.

### Non-goals
- No multi-tenant / public bot. A small allowlist of Telegram user IDs is the only auth model.
- No web UI, no database server (SQLite only), no Redis required.
- No third-party file hosting (the legacy project had a custom FastAPI upload server purely to dodge the 50 MB Bot API limit; the local Bot API server makes it unnecessary).
- No playlist support in v1 (single video per link; `noplaylist=True`).
- The legacy "music-library maintenance scripts" (Levenshtein folder reconciliation, Selenium YouTube search, ADB sync, bit-depth fixer) are **out of scope** — they belong in a separate repo.

---

## 2. User-facing features (functional spec)

### 2.1 Authorization
- Every update is checked against `ALLOWED_USER_IDS` (from settings) by an **outer middleware** on both message and callback-query routers. Unauthorized users get a single terse reply ("This bot is private.") on messages and a silent `callback.answer()` on callbacks; nothing else is processed.

### 2.2 Link intake
- Trigger: any text message containing a YouTube URL. Accept `youtube.com/watch?v=`, `youtu.be/`, `youtube.com/shorts/`, `youtube.com/embed/`, `music.youtube.com/watch?v=`, with or without scheme/`www`. Extract the 11-char video ID with a regex; do **not** call yt-dlp just to get the ID (the legacy code did a full `extract_info` network round-trip only to read `id`).
- Multiple links in one message → process the first, tell the user only one per message is supported.
- Non-link text → short help message listing what the bot does and the `/help`, `/status`, `/cancel`, `/cache` commands.

### 2.3 Choice flow (inline keyboards)
All choices are carried in **typed `CallbackData`** (aiogram 3 factories), not string splitting. Each intake creates a `Request` row (see §5) with a short random `req_id`; all callback data carries `req_id` so **several links can be in flight per user simultaneously** with no FSM collisions.

1. **Extract type**: `🎵 Audio` | `🎬 Video`
2. Audio → immediately enqueue. Video → fetch formats (§4.3), then:
3. **Quality**: up to 6 buttons, one per distinct height, labelled `1080p · mp4 · ~210 MB`, sorted descending; plus `🔙 Back`.
4. On enqueue, edit the choice message into the **status message**: `⏳ Queued — position 3`.

Stale callbacks (unknown/expired `req_id`, e.g. after a restart) answer with a toast `Request expired, send the link again.` and remove the keyboard.

### 2.4 Audio delivery
- **Single track** (no chapters): `send_audio` with `title`, `performer` (channel name), `duration`, `thumbnail`, filename `<Sanitized Title>.mp3`.
- **Multiple tracks** (chapters): default delivery is a **ZIP** named `<Sanitized Video Title>.zip` containing a folder `<Sanitized Video Title>/` with all MP3s plus `cover.jpg`. Provide a settings flag (`AUDIO_MULTI_DELIVERY = zip | individual | both`) — `individual` sends each track as its own audio message in order (useful for phones), `both` sends both.
- Because the local Bot API server allows 2 GB, **no size-based branching**. If a file exceeds `MAX_SEND_BYTES` (default 1.9 GB) the job fails with a clear message.

### 2.5 Video delivery
- `send_video` with `supports_streaming=True`, caption = title, filename `<Sanitized Title>.mp4`. Width/height/duration passed from yt-dlp info so Telegram shows a proper player.

### 2.6 Progress & status
One status message per job, edited in place (rate-limited to ≥ 4 s between edits, forced on phase transitions):

```
🛰 <Title>
Phase: Downloading  ▰▰▰▰▰▱▱▱▱▱ 52 %   3.1 MB/s
```
Phases: `Queued (pos N)` → `Downloading` → `Splitting i/N <chapter>` → `Tagging` → `Packaging` → `Uploading` → deleted on success (final result message replaces it) or turned into `❌ Failed: <reason>` on error (kept, so the user sees why).

### 2.7 Commands
- `/start`, `/help` — usage.
- `/status` — queue length, current job, cache size on disk, uptime.
- `/cancel` — cancel the user's queued jobs (not the one currently running; say so).
- `/cache <video_id|url>` — admin only: show whether cached; `/cache purge <id>` / `/cache purge all`.
- `/cookies` — admin only: reply with the cookie-file age and whether yt-dlp currently reports bot-detection. (Actual cookie refresh is done by file replacement on the host, §7.4.)

### 2.8 Cache behaviour
- Audio results are cached per video ID (§4.9). A second request for the same video skips download/split and goes straight to delivery — tell the user `⚡ Served from cache`.
- Video downloads are **not** cached (large, quality-dependent).

---

## 3. Architecture

```
┌──────────────┐   HTTPS    ┌─────────────────────┐  MTProto  ┌──────────┐
│  Telegram    │◄──────────►│ telegram-bot-api     │◄─────────►│ Telegram │
│  user        │            │ (self-hosted, --local│           │ DCs      │
└──────────────┘            └──────────▲───────────┘           └──────────┘
                                       │ HTTP (compose network)
                                       │ files passed BY PATH (shared volume)
                            ┌──────────┴───────────┐
                            │  chaptercut bot       │
                            │  ┌─────────────────┐  │
                            │  │ aiogram Dispatcher│ │  routers: commands, intake, choices
                            │  └────────┬────────┘  │  middlewares: auth, throttle
                            │           │ enqueue    │
                            │  ┌────────▼────────┐  │
                            │  │ JobQueue (SQLite)│  │  persistent, crash-safe
                            │  └────────┬────────┘  │
                            │           │ one job at a time
                            │  ┌────────▼────────┐  │
                            │  │ Worker           │  │  pipeline: fetch→download→split→tag→package→deliver
                            │  └──┬────┬────┬────┘  │
                            │     │    │    │        │
                            │  yt-dlp ffmpeg mutagen │  (yt-dlp & ffmpeg as subprocesses)
                            └───────────────────────┘
                                       │
                            ┌──────────▼───────────┐
                            │ /data volume          │
                            │  cache/<video_id>/    │  manifest.json + tracks + cover.jpg
                            │  work/<job_id>/       │  scratch, deleted after job
                            │  chaptercut.db        │  SQLite: requests, jobs, cache_entries
                            │  cookies.txt          │  operator-provided secret, read-only
                            └───────────────────────┘
```

### 3.1 Process model
- **One asyncio process.** The Dispatcher handles updates; a single `Worker` task consumes jobs sequentially (concurrency = 1 by default; `WORKER_CONCURRENCY` allows more, but CPU-bound ffmpeg makes >2 pointless on a small VPS).
- Blocking work never runs on the event loop:
  - yt-dlp runs **as a subprocess** (`yt-dlp --dump-single-json` / `--progress-template` parsing) rather than in-process. Rationale: YouTube extractor breakage and the occasional hard crash or hang must not take down the bot; subprocesses are killable with a timeout, and upgrading yt-dlp in the image never risks API drift inside our code. Wrap with `asyncio.create_subprocess_exec` + `asyncio.wait_for`.
  - ffmpeg/ffprobe likewise via `create_subprocess_exec`.
  - mutagen/Pillow calls are fast and synchronous; wrap in `asyncio.to_thread` anyway for consistency.
- Graceful shutdown on SIGTERM: stop accepting updates, let the current job finish up to `SHUTDOWN_GRACE_SECONDS` (default 120), then mark it `interrupted` and exit. Interrupted jobs are re-queued on next start.

### 3.2 Telegram transport
- aiogram `Bot(session=AiohttpSession(api=TelegramAPIServer.from_base(settings.bot_api_url, is_local=True)))`.
- Long polling (`start_polling`) by default; webhook not needed for a private bot.
- Files are sent with `FSInputFile(path)`; because the server runs `--local` and shares the `/data` volume, the server reads the file from disk directly. The path must be the path **as the bot-api container sees it**, so both containers mount the volume at the same mountpoint (`/data`).

### 3.3 Dependency direction
`bot/` → `queue/`, `cache/`, `pipeline/`. `pipeline/` must **not** import `bot/`; it reports progress through a small `ProgressSink` protocol (`async def update(phase, pct, detail)`), so the whole pipeline is testable without Telegram.

---

## 4. Processing pipeline (detailed)

Each job runs `Pipeline.run(job, sink)` which executes stages; each stage updates `job.phase` in SQLite and calls the sink.

### 4.1 Fetch metadata
`yt-dlp --dump-single-json --no-playlist --cookies /data/cookies.txt [extra args] URL` → parse JSON. Fields used: `id, title, uploader/channel, duration, thumbnail(s), chapters[], formats[], width, height, upload_date, webpage_url`. Timeout 120 s.

### 4.2 Download
- Record `downloaded_at = now(UTC)` once, at the start of this stage; it is reused by tagging (§4.6) and the manifest (§5.2).
- Audio: `-f bestaudio/best -x --audio-format mp3 --audio-quality 0` (VBR ~245 kbps; or `--audio-quality 192K` if `AUDIO_BITRATE` set). Output to `work/<job_id>/source.mp3`.
- Video: `-f <format_id>+bestaudio/best --merge-output-format mp4` → `work/<job_id>/video.mp4`. If the chosen format already contains audio, yt-dlp handles it.
- Progress: `--newline --progress-template "download:%(progress.downloaded_bytes)s/%(progress.total_bytes_estimate)s %(progress.speed)s"`; parse stdout lines into progress callbacks.
- Timeout `DOWNLOAD_TIMEOUT_SECONDS` (default 1800). On timeout kill the process group.

### 4.3 Format listing (video path only)
From `formats[]`: keep entries with `vcodec != "none"`, prefer `ext == "mp4"`, one per distinct `height`, compute size from `filesize` or `filesize_approx` (+ estimated best-audio size if the format is video-only), sort by height desc, take top 6. Cache the list on the `Request` row so the Back button doesn't refetch.

### 4.4 Chapter splitting (audio path)
- If `chapters` is empty → one track: `{title: video_title, start: 0, end: duration}`.
- Otherwise one track per chapter. `end_time` may be missing on the last chapter → use total duration from `ffprobe`.
- For each chapter `i`:
  ```
  ffmpeg -hide_banner -loglevel error -nostdin
         -ss <start> -to <end> -i source.mp3
         -c copy            # stream copy: no re-encode, fast, no generation loss
         -map_metadata -1   # strip; we write our own tags
         "out/<NN - Sanitized Title>.mp3"
  ```
  `-ss` before `-i` with `-c copy` seeks to the nearest frame; for MP3 that is accurate to ~26 ms — acceptable. (Legacy re-encoded every chapter with libmp3lame at 192k: slow and lossy.)
- Track numbering `NN` is zero-padded to the chapter-count width, used both in the filename and the `TRCK` tag (`i/N`).

### 4.5 Sanitization
- Titles for **tags** keep Unicode as-is.
- Titles for **filenames**: `unidecode` → replace any char not in `[A-Za-z0-9 ._-]` with a space → collapse whitespace → strip → truncate to 120 chars → fall back to `track_NN` if empty. Do **not** replace everything with underscores (legacy output like `Some_Song_Name_` was ugly).

### 4.6 Tagging (mutagen, ID3v2.4)
| Frame | Value |
|---|---|
| `TIT2` title | chapter title |
| `TPE1` artist | channel/uploader name |
| `TALB` album | video title |
| `TPE2` album artist | channel name |
| `TRCK` | `i/N` |
| `TDRC` year | from `upload_date` (YouTube publish date) |
| `COMM` (lang `eng`, desc empty) | `Source: <webpage_url>\nDownloaded: <YYYY-MM-DD>` — human-readable; this is the field most players show as "Comment" |
| `TXXX:SOURCE_URL` | webpage_url (machine-readable) |
| `TXXX:DOWNLOADED_AT` | ISO-8601 UTC timestamp of the download, e.g. `2026-08-23T12:00:00Z` (machine-readable) |
| `TXXX:VIDEO_ID` | YouTube video ID |
| `APIC` | cover JPEG, front cover |

The download timestamp comes from `downloaded_at` captured in §4.2, so it is identical across all tracks of one video. It is also persisted in `manifest.json` as `downloaded_at`, so **cached re-deliveries keep the original download date**, not the re-serve date. The legacy project stored this only by abusing the album field (`"<url> (<date>)"`) — do not do that; `TALB` stays the video title.

Cover: download the best thumbnail → Pillow → convert to RGB → if aspect isn't ~1:1, **center-crop to square** (`COVER_SQUARE=true`, default on) → resize to max 1000×1000 → JPEG q=90 → save as `cover.jpg`.

### 4.7 File mtimes
Set each track's mtime to `downloaded_at + i*2 s` so players that sort "by date added" keep album order (legacy feature worth keeping). Use `os.utime`. Re-apply the same mtimes when serving from cache, since copy/zip operations may reset them.

### 4.8 Packaging
ZIP with `zipfile.ZIP_STORED` (MP3 doesn't compress; stored is faster). Inner folder name = sanitized video title. Include `cover.jpg`. Preserve per-file mtimes in the ZIP entries.

### 4.9 Deliver → cache → cleanup
- On success: move `work/<job>/out/` to `cache/<video_id>.tmp/`, write `manifest.json`, then **`os.rename` to `cache/<video_id>/`** (atomic on POSIX). Only a directory containing a valid `manifest.json` counts as cached; anything else is garbage to be swept.
- Delete `work/<job_id>/` unconditionally in a `finally`.
- Startup sweep: delete every `work/*`, every `cache/*.tmp`, and any `cache/<id>` without a valid manifest.
- Cache eviction: `CACHE_MAX_BYTES` (default 20 GB) — evict least-recently-served (`last_served_at` in `cache_entries`).

---

## 5. Data & persistence

### 5.1 SQLite (`/data/chaptercut.db`, via `aiosqlite` with a small hand-written repository layer; no heavyweight ORM)

```sql
CREATE TABLE requests (
  req_id        TEXT PRIMARY KEY,   -- 8-char urlsafe token
  user_id       INTEGER NOT NULL,
  chat_id       INTEGER NOT NULL,
  url           TEXT NOT NULL,
  video_id      TEXT NOT NULL,
  extract_type  TEXT,               -- audio|video|NULL
  formats_json  TEXT,               -- cached format list
  created_at    TEXT NOT NULL,
  expires_at    TEXT NOT NULL       -- now+1h; sweep expired rows
);

CREATE TABLE jobs (
  job_id          TEXT PRIMARY KEY,
  req_id          TEXT REFERENCES requests(req_id),
  user_id         INTEGER NOT NULL,
  chat_id         INTEGER NOT NULL,
  status_msg_id   INTEGER,
  kind            TEXT NOT NULL,    -- audio|video
  video_id        TEXT NOT NULL,
  url             TEXT NOT NULL,
  format_id       TEXT,
  state           TEXT NOT NULL,    -- queued|running|done|failed|cancelled|interrupted
  phase           TEXT,             -- download|split|tag|package|upload
  error           TEXT,
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT
);
CREATE INDEX jobs_state_idx ON jobs(state, created_at);

CREATE TABLE cache_entries (
  video_id        TEXT PRIMARY KEY,
  title           TEXT,
  bytes           INTEGER,
  tracks          INTEGER,
  downloaded_at   TEXT,
  last_served_at  TEXT
);
```

Queue semantics: the worker does `SELECT … WHERE state='queued' ORDER BY created_at LIMIT 1` and flips it to `running` in the same transaction; an `asyncio.Event` is set on enqueue so the worker wakes immediately instead of polling on a timer (with a 30 s fallback poll). On startup, `running`/`interrupted` → `queued`. Use WAL mode.

### 5.2 `manifest.json` (inside `cache/<video_id>/`)
```json
{
  "schema": 1,
  "video_id": "dQw4w9WgXcQ",
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "title": "…", "uploader": "…", "upload_date": "2009-10-25",
  "duration_ms": 212000,
  "cover": "cover.jpg",
  "tracks": [
    {"n": 1, "file": "01 - Intro.mp3", "title": "Intro", "start_ms": 0, "end_ms": 61000}
  ],
  "downloaded_at": "2026-08-23T12:00:00Z"
}
```
Validate with a pydantic model on read; invalid → treat as not cached and delete.

---

## 6. Repository layout

```
chaptercut/
├── README.md
├── LICENSE                         # MIT
├── pyproject.toml                  # uv-managed; deps, ruff, pyright, pytest config
├── uv.lock
├── .python-version                 # 3.12
├── .env.example                    # every setting, documented, NO real values
├── .gitignore                      # .env, data/, *.db, cookies.txt, tokens.json, __pycache__, .venv
├── .pre-commit-config.yaml         # ruff, ruff-format, pyright, gitleaks
├── .gitleaks.toml                  # extra rule: telegram token regex \d{8,10}:[A-Za-z0-9_-]{35}
├── .github/workflows/ci.yml        # uv sync, ruff, pyright, pytest, docker build
├── Dockerfile                      # python:3.12-slim + ffmpeg + yt-dlp; non-root user
├── docker-compose.yml              # bot + telegram-bot-api, shared /data volume
├── justfile                        # dev, test, lint, up, down, logs, logout-cloud
├── docs/
│   ├── architecture.md             # this spec, trimmed to what stays true
│   ├── bot-api-server.md           # why local server, logOut migration steps
│   └── lessons-learned.md          # §9 of this document
├── src/chaptercut/
│   ├── __init__.py
│   ├── __main__.py                 # `python -m chaptercut` → main()
│   ├── main.py                     # build settings, bot, dispatcher, worker; run; signal handling
│   ├── settings.py                 # pydantic-settings Settings (§7)
│   ├── logging.py                  # structlog / stdlib JSON config
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── factory.py              # create_bot(), create_dispatcher()
│   │   ├── middlewares/
│   │   │   ├── auth.py             # allowlist middleware
│   │   │   └── throttle.py         # per-user rate limit (token bucket, in-memory)
│   │   ├── routers/
│   │   │   ├── commands.py         # /start /help /status /cancel /cache /cookies
│   │   │   ├── intake.py           # YouTube link handler → Request → type keyboard
│   │   │   └── choices.py          # callback handlers: type, quality, back
│   │   ├── callbacks.py            # CallbackData classes: TypeCb, QualityCb, BackCb
│   │   ├── keyboards.py            # builders for the inline keyboards
│   │   ├── texts.py                # all user-facing strings in one place
│   │   └── progress.py             # StatusMessage: rate-limited edit_text, phase bar; implements ProgressSink
│   ├── queue/
│   │   ├── __init__.py
│   │   ├── db.py                   # aiosqlite connection, migrations (schema v1)
│   │   ├── models.py               # Request, Job dataclasses / pydantic
│   │   ├── repository.py           # enqueue, claim_next, mark_*, cancel_for_user, requeue_interrupted
│   │   └── worker.py               # Worker task: claim → Pipeline.run → finalize
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── runner.py               # Pipeline.run(job, sink): orchestrates stages
│   │   ├── sink.py                 # ProgressSink protocol + NullSink for tests
│   │   ├── ytdlp.py                # subprocess wrapper: info(), download(), progress parsing, bot-check detection
│   │   ├── formats.py              # select_video_formats(info) → list[FormatOption]
│   │   ├── chapters.py             # chapters_from_info(), Track model
│   │   ├── ffmpeg.py               # probe_duration(), cut(), async subprocess helpers
│   │   ├── tagging.py              # write_tags(track, meta, cover, downloaded_at)
│   │   ├── cover.py                # fetch_and_normalize_cover()
│   │   ├── sanitize.py             # safe_filename(), safe_title()
│   │   ├── package.py              # make_zip()
│   │   └── deliver.py              # send_audio/send_video/send_document via FSInputFile
│   ├── cache/
│   │   ├── __init__.py
│   │   ├── manifest.py             # Manifest pydantic model
│   │   └── store.py                # CacheStore: get(), commit_atomic(), sweep(), evict(), usage()
│   └── util/
│       ├── youtube.py              # extract_video_id(), is_youtube_url()
│       ├── paths.py                # data dir layout helpers
│       └── timefmt.py
├── tests/
│   ├── conftest.py                 # tmp data dir, settings fixture, fake bot
│   ├── test_youtube_ids.py
│   ├── test_sanitize.py
│   ├── test_chapters.py            # no chapters / missing end_time / ordering
│   ├── test_formats.py             # dedupe by height, sorting, size estimate
│   ├── test_tagging.py             # every frame in §4.6 present, incl. DOWNLOADED_AT and COMM text
│   ├── test_manifest.py
│   ├── test_cache_store.py         # atomic commit, sweep of .tmp, invalid manifest
│   ├── test_repository.py          # enqueue/claim/cancel/restart-requeue
│   ├── test_progress.py            # rate limiting of edits
│   └── integration/
│       └── test_pipeline_ffmpeg.py # marker: requires ffmpeg; cuts a generated sine-wave mp3
└── data/                           # gitignored runtime volume (created at runtime)
    ├── cache/
    ├── work/
    ├── chaptercut.db
    └── cookies.txt                 # provided by operator, never committed
```

---

## 7. Configuration (`settings.py`, pydantic-settings, env prefix `CC_`)

```
CC_BOT_TOKEN=                       # required, from @BotFather
CC_BOT_API_URL=http://bot-api:8081  # local telegram-bot-api; set https://api.telegram.org to use cloud (50 MB limit)
CC_BOT_API_LOCAL=true               # pass is_local=True (files by path)
CC_ALLOWED_USER_IDS=111,222         # required; comma-separated
CC_ADMIN_USER_IDS=111               # subset; may use /cache purge, /cookies
CC_DATA_DIR=/data
CC_COOKIES_FILE=/data/cookies.txt   # optional; if missing, run without cookies
CC_YTDLP_EXTRA_ARGS=                # optional, e.g. --extractor-args "youtube:player_client=default,mweb"
CC_AUDIO_BITRATE=                   # empty = VBR best; or e.g. 192K
CC_AUDIO_MULTI_DELIVERY=zip         # zip|individual|both
CC_COVER_SQUARE=true
CC_MAX_SEND_BYTES=1900000000
CC_CACHE_MAX_BYTES=21474836480
CC_WORKER_CONCURRENCY=1
CC_DOWNLOAD_TIMEOUT_SECONDS=1800
CC_SHUTDOWN_GRACE_SECONDS=120
CC_RATE_LIMIT_PER_MINUTE=20
CC_LOG_LEVEL=INFO
CC_LOG_JSON=false
# telegram-bot-api container (compose only, from https://my.telegram.org)
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
```

`.env.example` carries all keys with comments and placeholder values. `Settings` validates: token shape regex, at least one allowed user, admins ⊆ allowed, data dir writable.

### 7.4 Cookies / bot-detection handling
- YouTube frequently blocks datacenter IPs ("Sign in to confirm you're not a bot"). Mitigation is operational, not code: the operator exports a Netscape `cookies.txt` from a logged-in browser (ideally a throwaway Google account) and places it at `CC_COOKIES_FILE`. The bot reads it read-only; it never writes, never commits, never transmits it.
- Optionally support the yt-dlp **PO-token provider plugin** (`bgutil-ytdlp-pot-provider`) by adding it to the Docker image and exposing `CC_YTDLP_EXTRA_ARGS`; document in `docs/bot-api-server.md`. Do **not** build a custom token-upload API (legacy did; it was unnecessary and a secret-handling liability).
- Detect the bot-check error string in yt-dlp stderr and surface it distinctly to the user/admin (`⚠️ YouTube requires fresh cookies`).

---

## 8. Deployment

### 8.1 Dockerfile
- `python:3.12-slim`, `apt-get install ffmpeg`, `pip install uv`, `uv sync --frozen --no-dev`, `yt-dlp` pinned in `pyproject` but upgraded at image build (`uv pip install -U yt-dlp`), non-root user `app`, `ENTRYPOINT ["python","-m","chaptercut"]`.

### 8.2 docker-compose.yml
```yaml
services:
  bot-api:
    image: aiogram/telegram-bot-api:latest
    restart: unless-stopped
    environment:
      TELEGRAM_API_ID: ${TELEGRAM_API_ID}
      TELEGRAM_API_HASH: ${TELEGRAM_API_HASH}
      TELEGRAM_LOCAL: 1
    volumes:
      - data:/data                      # shared so FSInputFile paths resolve identically
      - botapi:/var/lib/telegram-bot-api
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    depends_on: [bot-api]
    volumes:
      - data:/data
volumes: { data: {}, botapi: {} }
```
Note: the bot-api container may run as a different uid; ensure `/data` is group-writable or run both with the same `user:`.

### 8.3 One-time cloud → local migration
A bot token is bound to one Bot API server at a time. Provide `just logout-cloud` which runs a tiny script: `Bot(token).log_out()` against `https://api.telegram.org`, then prints "wait ~10 minutes, then start compose". Document the reverse (`close()`), and that after `logOut` the cloud server won't accept the token for a while.

### 8.4 Operations
- `just logs`, `just up`, `just down`, `just shell`.
- Healthcheck: bot touches `/data/heartbeat` every 30 s; compose healthcheck checks its age.
- Log rotation via Docker json-file driver options.

---

## 9. Lessons learned from the predecessor — MUST be honoured

These are real problems found in the legacy codebase during an audit. Each maps to a requirement above.

### 9.1 Secrets and identity leaked into git history
- **What happened**: a Python config module containing the live Telegram bot token, a file-exchange auth key, the server's public IP, the owner's Telegram user IDs, and a database password was committed on the second commit and stayed tracked for the life of the repo. A container-registry personal access token was committed as a plain file. A browser-exported `cookies.txt` containing full Google login session cookies was committed once in a "backup" commit, then gitignored — but remained in history. An entire virtualenv was committed. Commits carried a personal email plus a second malformed identity. All of it was pushed to a remote.
- **Consequence**: the repo could never be made public; every credential had to be rotated; the clean solution was abandoning the history entirely (this project).
- **Requirements**:
  1. Secrets only via environment (`pydantic-settings`); the repo contains `.env.example` only.
  2. `.gitignore` in the **first commit** includes `.env`, `data/`, `cookies.txt`, `tokens.json`, `*.db`, `.venv/`.
  3. `gitleaks` pre-commit hook with a custom Telegram-token rule; enable GitHub push protection.
  4. Never write a "token uploader" that ships credentials across the network as part of the app.
  5. Commit as a GitHub noreply identity; set `git config user.email` before the first commit.
  6. Treat `cookies.txt` as equivalent to a password: mounted read-only, never logged, never echoed in `/cookies` output (only its age/size).

### 9.2 The 50 MB limit drove architecture in the wrong direction
- **What happened**: to deliver files >50 MB, a separate FastAPI upload server with shared-key auth, random links and a cleanup task was built, plus dual upload code paths, a "3rd party" user choice, and size-based branching everywhere.
- **Requirement**: self-hosted Bot API server (§3.2). One delivery path. The concept of an "upload method" does not exist in the UX.

### 9.3 Non-atomic cache writes poisoned the cache
- **What happened**: the cache directory for a video was created early (to store the thumbnail) and the manifest written last. A crash/stop in between left a directory that *looked* cached; every subsequent request for that video skipped processing and then crashed reading the missing manifest, until someone deleted the folder by hand.
- **Requirement**: §4.9 — build in a `.tmp` dir, rename atomically, "cached" ⇔ valid manifest, sweep on startup.

### 9.4 In-memory queue lost jobs on restart; temp files were orphaned
- **What happened**: `asyncio.Queue` + cleanup only on the success path. Any stop mid-job lost queued work, left stale "position N" messages in chat, and left multi-hundred-MB files in `temp/` and working folders forever (no startup sweep). Video download files also leaked on ordinary failures because the `except` branch didn't delete them.
- **Requirement**: §5.1 persistent queue with re-queue on start; `finally`-based cleanup of `work/<job_id>`; startup sweep; `/status` shows disk usage.

### 9.5 FSM state collided across concurrent requests
- **What happened**: early versions used aiogram FSM states for the choice flow; sending a second link while the first was mid-dialogue corrupted both. It was patched with a global dict keyed by message ID and `"type|audio|123"` string callbacks parsed by `split('|')`.
- **Requirement**: typed `CallbackData` + `req_id` persisted in SQLite (§2.3, §5.1). No FSM for the link flow.

### 9.6 Blocking calls and in-process yt-dlp
- **What happened**: yt-dlp ran in-process via `run_in_executor`; a format listing ran *synchronously inside a callback handler*, freezing the whole bot for seconds per click. An extra full `extract_info` network call was done just to get the video ID.
- **Requirement**: §3.1 — yt-dlp and ffmpeg as async subprocesses with timeouts; video ID from regex; nothing blocking in handlers.

### 9.7 Re-encoding every chapter
- **What happened**: each chapter was re-encoded with libmp3lame, slow and lossy.
- **Requirement**: §4.4 stream copy.

### 9.8 Duplicate code paths
- **What happened**: two near-identical audio processing functions (an old handler path and the worker path) drifted apart; a second "tag an uploaded audio with a cover" feature existed half-wired and disabled.
- **Requirement**: one pipeline; features are either in scope and tested, or not present.

### 9.9 Metadata stuffed into the wrong tag
- **What happened**: the download date and source URL were stored by overwriting the album tag with `"<url> (<date>)"`, so every player showed a URL as the album name.
- **Requirement**: §4.6 — album is the video title; provenance and download date go in `COMM` (human-readable) and `TXXX:*` frames (machine-readable).

### 9.10 Misc hygiene
- `requirements.txt` was UTF-16 with BOM (breaks `pip install -r` on Linux); a committed `runtime.txt` pinned an unrelated Python version; the start script referenced a venv name that didn't match `.gitignore`. → `pyproject.toml` + `uv.lock` only, UTF-8 everywhere, Docker is the runtime.
- eyeD3 (semi-maintained) and mutagen were both used. → mutagen only.
- String paths everywhere. → `pathlib.Path`.
- Config for a MySQL database that was never actually used. → no dead config.
- Personal music-library filenames and link lists were committed. → no personal data in the repo; test fixtures are synthetic.

---

## 10. Quality bar

- `ruff` (lint + format; `select = ["E","F","I","UP","B","ASYNC","S"]`), `pyright` strict on `src/`, `pytest` ≥ 80 % coverage on `pipeline/`, `cache/`, `queue/`, `util/`.
- Every user-facing string lives in `bot/texts.py`.
- Every subprocess call has a timeout and logs stderr on failure.
- Structured logs with `job_id`, `video_id`, `user_id` bound as context; never log tokens, cookies, or full URLs with query strings beyond the video ID.
- README covers: what it does (screenshot/GIF placeholder), quick start with compose, the cloud→local logOut step, cookie setup, settings table, architecture diagram, and a "Security notes" section.

---

## 11. Milestones (suggested order for the builder)

1. **Skeleton**: pyproject, settings, logging, Dockerfile, compose, CI, pre-commit, README stub. Bot answers `/start` against the local Bot API server.
2. **Intake + choices**: URL parsing, `requests` table, keyboards, typed callbacks, auth middleware, tests for youtube IDs/sanitize.
3. **Queue + worker + progress**: SQLite queue, worker loop, `StatusMessage`, restart re-queue, cancel, tests.
4. **Audio pipeline**: yt-dlp subprocess wrapper, chapters, ffmpeg cut, cover, tagging (all §4.6 frames), zip, deliver; integration test with a generated tone file.
5. **Cache**: atomic store, sweep, eviction, `/cache` admin commands, `⚡ cached` path.
6. **Video pipeline**: formats listing, quality keyboard, download+merge, `send_video`.
7. **Ops polish**: `/status`, heartbeat healthcheck, cookie-age reporting, bot-detection error surfacing, docs.

---

## 12. Glossary
- **Bot API server**: Telegram's HTTP façade over MTProto; `telegram-bot-api` is its open-source, self-hostable build.
- **`--local` mode**: Bot API server flag allowing file paths instead of uploads and lifting size limits to 2 GB.
- **Chapters**: timestamped sections YouTube derives from the description or creator markers; yt-dlp exposes them as `chapters[]`.
- **PO token**: YouTube "proof of origin" token that yt-dlp may need on datacenter IPs; handled by a yt-dlp plugin, not by this project.
