# chaptercut

A private Telegram bot that turns a YouTube link into properly tagged MP3s,
split along the video's chapter markers, or into a downloaded video file.

Paste a link, pick `Audio` or `Video`, get the result back in the chat.

```
you   https://www.youtube.com/watch?v=...
bot   [ Audio ] [ Video ]
bot   Splitting 4/12  Nocturne in E flat
bot   [ Some Album.zip ]  12 tracks, 68.4 MB
```

> Screenshot / GIF placeholder.

## What it does

- Splits an album upload into one MP3 per chapter, stream-copied (no re-encode,
  no generation loss).
- Writes full ID3v2.4 tags: title, artist, album, album artist, track number,
  year, cover art, plus the source URL and download date in their proper
  frames rather than smuggled into the album name.
- Caches processed audio per video id, so the second request for the same
  video is instant.
- Survives restarts: the job queue lives in SQLite, and interrupted jobs are
  re-queued on the next start.
- Downloads video at a quality you pick from the actual available formats.

## Quick start

Requires Docker, a bot token from [@BotFather](https://t.me/BotFather), and an
API id and hash from [my.telegram.org](https://my.telegram.org).

```bash
git clone https://github.com/<you>/chaptercut && cd chaptercut
cp .env.example .env
$EDITOR .env          # token, your Telegram user id, API id and hash
```

If the token has ever talked to Telegram's cloud Bot API server, release it
first, then wait about ten minutes:

```bash
just logout-cloud
```

Then:

```bash
just up        # docker compose up -d --build
just logs
```

Send `/start` to the bot from an allowlisted account.

## Why a self-hosted Bot API server

Telegram's cloud Bot API caps bot uploads at 50 MB. A chapter-split album is
routinely larger than that. Running the open-source `telegram-bot-api` server
in `--local` mode raises the cap to 2 GB and lets the bot hand over files by
path instead of uploading them, so there is exactly one delivery path and no
external file host. See [docs/bot-api-server.md](docs/bot-api-server.md).

## Cookies

YouTube blocks datacenter IPs with "Sign in to confirm you're not a bot". The
fix is operational: export a Netscape `cookies.txt` from a browser logged into
a throwaway Google account and drop it in the data volume at
`CC_COOKIES_FILE`. The bot reads it, never writes it, never logs it, and
`/cookies` reports only its age and size.

## Development

```bash
uv sync
just test
just lint
```

Layout: `bot/` talks to Telegram, `queue/` owns the SQLite job queue, `cache/`
owns the on-disk result cache, and `pipeline/` does the work. `pipeline/` never
imports `bot/`; it reports progress through a `ProgressSink` protocol, so the
whole thing runs headless under test.

## Settings

Every setting is an environment variable with the `CC_` prefix; see
[.env.example](.env.example) for the annotated list.

| Setting | Default | Meaning |
|---|---|---|
| `CC_BOT_TOKEN` | required | Token from @BotFather |
| `CC_BOT_API_URL` | `http://bot-api:8081` | Bot API server base URL |
| `CC_BOT_API_LOCAL` | `true` | Hand files to the server by path |
| `CC_ALLOWED_USER_IDS` | required | Comma-separated Telegram user ids |
| `CC_ADMIN_USER_IDS` | required | Subset allowed to use `/cache` and `/cookies` |
| `CC_DATA_DIR` | `/data` | Runtime volume |
| `CC_COOKIES_FILE` | `/data/cookies.txt` | Optional yt-dlp cookie jar |
| `CC_YTDLP_EXTRA_ARGS` | empty | Extra yt-dlp CLI args |
| `CC_AUDIO_BITRATE` | empty (VBR best) | e.g. `192K` |
| `CC_AUDIO_MULTI_DELIVERY` | `zip` | `zip`, `individual`, or `both` |
| `CC_COVER_SQUARE` | `true` | Center-crop cover art to a square |
| `CC_MAX_SEND_BYTES` | `1900000000` | Refuse to send anything larger |
| `CC_CACHE_MAX_BYTES` | `21474836480` | Evict least-recently-served above this |
| `CC_DOWNLOAD_TIMEOUT_SECONDS` | `1800` | Per-download timeout |
| `CC_SHUTDOWN_GRACE_SECONDS` | `120` | Time the running job gets on SIGTERM |
| `CC_RATE_LIMIT_PER_MINUTE` | `20` | Per-user message allowance |

## Architecture

See [docs/architecture.md](docs/architecture.md), and
[docs/lessons-learned.md](docs/lessons-learned.md) for the predecessor's
mistakes this rewrite exists to avoid.

## Security notes

- No secret is ever in the repo. Configuration comes from the environment via
  `pydantic-settings`; only `.env.example` is committed.
- `.env`, `data/`, `cookies.txt`, `*.db` are gitignored from the first commit.
- A `gitleaks` pre-commit hook and a CI job scan for Telegram tokens and
  cookie jars specifically. Enable GitHub push protection on the remote too.
- The allowlist is the entire auth model. Anyone not in `CC_ALLOWED_USER_IDS`
  gets one terse reply and nothing else is processed.
- Cookies are treated as a password: mounted read-only, never logged, never
  echoed back, never transmitted anywhere but to yt-dlp on disk.
- Logs carry video ids, never full URLs with query strings, and never tokens.

## License

MIT. See [LICENSE](LICENSE).
