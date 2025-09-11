# The self-hosted Bot API server

## Why

Telegram's cloud Bot API caps bot uploads at 50 MB. A chapter-split album is
routinely larger than that.

The predecessor project answered this by building a separate FastAPI upload
server with shared-key auth, random links, and a cleanup task, plus dual upload
code paths and a "where should I put this" choice in the UX. That is a lot of
machinery, and a standing credential, for a limit that is configurable.

`telegram-bot-api` is Telegram's own Bot API implementation, open source and
self-hostable. Run with `--local` it:

- raises the upload limit to 2 GB;
- accepts a **local file path** instead of an upload, so the bot hands over a
  path on the shared volume and the server reads it directly.

So there is exactly one delivery path, no external file host, and no size-based
branching anywhere in the code.

## Setup

1. Get an `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org).
   These are **not** the same as the bot token from @BotFather.
2. Put them in `.env` as `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`.
3. Point the bot at the container: `CC_BOT_API_URL=http://bot-api:8081` and
   `CC_BOT_API_LOCAL=true`.

Both containers mount the same `data` volume at `/data` and run as the same
uid, because the path the bot sends must resolve identically on the server
side, and both processes write there.

## Migrating a token from the cloud server

A bot token is bound to one Bot API server at a time. If the token has ever
talked to `api.telegram.org`, that server has to release it first:

```bash
just logout-cloud
```

This calls `logOut` against the cloud server. Then **wait about ten minutes**
before starting compose; the cloud server will not release the token
immediately, and the local server will report an authorization error until it
does.

### Going back

To move the token back to the cloud, call `close` against the **local** server
first, wait, then start against `api.telegram.org` with
`CC_BOT_API_LOCAL=false`. Settings validation refuses `bot_api_local=true`
together with the cloud URL, because file paths would be meaningless there.

## Bot detection and cookies

YouTube blocks datacenter IP ranges with "Sign in to confirm you're not a bot".
The fix is operational, not a code change.

1. Log into YouTube in a browser with a **throwaway** Google account.
2. Export cookies in Netscape format with a browser extension.
3. Place the file on the host at the path `CC_COOKIES_FILE` points to, inside
   the data volume.

The bot reads the file, never writes it, never logs it, and never sends it
anywhere but to yt-dlp on the local disk. `/cookies` reports only its size and
age. Treat the file exactly as you would a password: it carries a live Google
session. It is in `.gitignore` and in the gitleaks rules; the predecessor
committed one and it stayed in the history forever.

When yt-dlp fails with a bot check, the bot recognises it and replies with a
distinct message rather than a generic failure, so it is obvious that the
cookies need refreshing rather than that the video is broken.

### PO tokens

yt-dlp may also need a "proof of origin" token on datacenter IPs. This is
handled by a yt-dlp plugin, not by this project. Add
`bgutil-ytdlp-pot-provider` to the image and pass whatever extra flags it needs
through `CC_YTDLP_EXTRA_ARGS`, for example:

```
CC_YTDLP_EXTRA_ARGS=--extractor-args "youtube:player_client=default,mweb"
```

Do not build a custom token-upload API. The predecessor did, and it was both
unnecessary and a place for credentials to leak.

## Operations

```bash
just up       # build and start
just logs     # follow the bot
just shell    # a shell in the bot container
just down     # stop
```

The bot touches `/data/heartbeat` every 30 seconds and the compose healthcheck
fails the container if that file goes stale, which catches a wedged event loop
that a process-liveness check would not.

Docker's json-file log driver is capped at 3 files of 10 MB for both services.
