# Architecture

## Shape

```
Telegram user  <-HTTPS->  telegram-bot-api (self-hosted, --local)  <-MTProto->  Telegram
                                     ^
                                     | HTTP on the compose network
                                     | files handed over BY PATH (shared volume)
                          +----------+-----------+
                          |  chaptercut bot      |
                          |  aiogram Dispatcher  |  routers: commands, choices, intake
                          |          |           |  middlewares: auth, throttle
                          |  JobQueue (SQLite)   |  persistent, crash-safe
                          |          |           |
                          |  Worker              |  fetch -> download -> split ->
                          |                      |  tag -> package -> deliver
                          +----------+-----------+
                                     |
                          /data volume
                            cache/<prov>-<id>/  manifest.json + tracks + cover.jpg
                            work/<job_id>/      scratch, deleted after every job
                            chaptercut.db       requests, jobs, cache_entries
                            cookies.txt         operator-provided, read-only
```

## Process model

One asyncio process. The Dispatcher handles updates while a single `Worker`
task consumes jobs sequentially.

Nothing blocking runs on the event loop:

- **yt-dlp runs as a subprocess**, not in-process. YouTube extractor breakage
  is routine and yt-dlp occasionally hangs or dies hard; a subprocess is
  killable with a timeout, and upgrading it in the image can never cause API
  drift inside our code.
- **ffmpeg and ffprobe** likewise, through `asyncio.create_subprocess_exec`.
- mutagen and Pillow are fast but synchronous, so they go through
  `asyncio.to_thread` for consistency.

Every subprocess has a timeout, and a timeout kills the whole process group.

On SIGTERM the bot stops polling, lets the running job finish for up to
`CC_SHUTDOWN_GRACE_SECONDS`, then marks it interrupted and exits. Interrupted
jobs are re-queued on the next start.

## Providers

`providers/` holds one small class per source site: how to recognise its links,
what the id is, and what a canonical URL looks like. Nothing else in the system
is site-specific. See [providers.md](providers.md).

## Dependency direction

`bot/` may import `queue/`, `cache/`, and `pipeline/`. Both may import
`providers/`, which is a leaf and imports nothing of ours. `pipeline/` must
never import `bot/`: it reports progress through the `ProgressSink` protocol,
so the whole pipeline runs headless under test with no aiogram involved.

Delivery lives in `bot/deliver.py` rather than `pipeline/`, because sending
files is a Telegram concern and the rule above is the one that matters.

## Telegram transport

aiogram is pointed at the configured server with
`TelegramAPIServer.from_base(url, is_local=True)`. Because the server runs
`--local` and shares the `/data` volume, files go out as `FSInputFile` paths
that the server reads directly from disk. Both containers therefore mount the
volume at the same path, or the paths would not resolve on the server side.

Long polling; a private bot has no reason to run a webhook.

## The job queue

`jobs` and `requests` live in SQLite (WAL mode) via `aiosqlite` and a small
hand-written repository. The worker claims with a single `BEGIN IMMEDIATE`
transaction that selects the oldest queued row and flips it to `running`, so
two workers could never take the same job.

An `asyncio.Event` wakes the worker on enqueue, with a 30 second fallback poll.
The event is cleared *before* claiming, not after, so a wake that arrives
between an empty claim and the wait is not lost.

On startup, everything left `running` or `interrupted` goes back to `queued`.

## The choice flow

Each link creates a `Request` row with a short random `req_id`. Every button
carries that `req_id` in typed `CallbackData`. There is no FSM state, so
several links can be mid-dialogue at once without colliding, and a callback
for a request that has expired or been swept answers with a toast and removes
its keyboard instead of failing.

The video format list is cached on the request row, so the Back button never
re-runs yt-dlp.

## The cache

A directory under `cache/<provider>-<media_id>/` counts as cached only when it
holds a valid `manifest.json` whose track files all exist. Results are built in
`work/<job_id>/out/`, moved to a sibling `.tmp/` directory, given their
manifest, and then renamed into place. The rename is atomic, so a crash can
leave a `.tmp` directory but never a half-populated entry that looks valid.

Keys are namespaced by provider, and `cache_entries` has a composite primary
key for the same reason: a media id is only unique within one site.

Startup sweeps `work/*`, every `cache/*.tmp`, and any cache directory without a
valid manifest.

Video downloads are not cached: they are large and quality-dependent.

## Provenance

Tags follow the table in the build spec. The two things worth restating:
`TALB` is the video title and nothing else, and the download timestamp is
captured once at the start of the download, written to `TXXX:DOWNLOADED_AT`
and to `manifest.json`. A cache re-delivery therefore reports the original
download date rather than the date it was served again.

Track mtimes are staggered two seconds apart in track order so players that
sort by "date added" keep album order, and they are re-applied when serving
from cache because copying and zipping reset them.
