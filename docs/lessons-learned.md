# Lessons learned from the predecessor

This project is a ground-up rewrite. These are the real problems found in the
codebase it replaces, and what this one does instead. Each is a requirement,
not a preference.

## 1. Secrets and identity in git history

**What happened.** A config module holding the live bot token, a file-exchange
auth key, the server's public IP, the owner's Telegram user ids, and a database
password was committed on the second commit and stayed tracked for the life of
the repo. A container-registry access token was committed as a plain file. A
browser-exported `cookies.txt` with full Google session cookies was committed
in a "backup" commit, then gitignored, but stayed in history. An entire
virtualenv was committed. Commits carried a personal email plus a second
malformed identity. All of it was pushed.

**Consequence.** The repo could never be made public, every credential had to
be rotated, and the only clean answer was abandoning the history entirely.
Which is this project.

**What we do.**

- Secrets only through the environment, via `pydantic-settings`. The repo
  contains `.env.example` and nothing else.
- `.gitignore` covers `.env`, `data/`, `cookies.txt`, `tokens.json`, `*.db`,
  and `.venv/` **in the first commit**.
- A `gitleaks` pre-commit hook and CI job, with extra rules for Telegram
  tokens and Netscape cookie jars. Enable GitHub push protection on the remote.
- No component ever ships a credential across the network as part of its job.
- `git config user.email` is set to a noreply identity before the first commit.
- `cookies.txt` is treated as a password: mounted read-only, never logged,
  and `/cookies` reports only size and age.

## 2. The 50 MB limit drove the architecture

**What happened.** To deliver files over 50 MB, a separate FastAPI upload
server was built, with shared-key auth, random links, and a cleanup task, plus
dual upload code paths and a "3rd party" choice exposed to the user.

**What we do.** A self-hosted `telegram-bot-api` in `--local` mode. 2 GB limit,
files passed by path. One delivery path. The concept of an "upload method"
does not exist in the UX. Over `CC_MAX_SEND_BYTES` is a clear failure, not a
fallback to some other transport.

## 3. Non-atomic cache writes poisoned the cache

**What happened.** The cache directory for a video was created early, to hold
the thumbnail, and the manifest was written last. A crash in between left a
directory that *looked* cached. Every later request for that video skipped
processing and then crashed reading the missing manifest, until someone deleted
the folder by hand.

**What we do.** Build in `work/<job_id>/out/`, move to `cache/<id>.tmp/`, write
the manifest, then `os.rename` into place. "Cached" means a valid manifest
whose files exist, and nothing else. Startup sweeps `.tmp` directories and any
entry that fails validation. Tested in `tests/test_cache_store.py`.

## 4. An in-memory queue lost jobs, and temp files leaked

**What happened.** An `asyncio.Queue` plus cleanup only on the success path.
Any stop mid-job lost queued work, left stale "position N" messages in chat,
and left hundreds of megabytes in `temp/` forever, with no startup sweep. Video
downloads leaked on ordinary failures too, because the `except` branch did not
delete them.

**What we do.** A persistent SQLite queue, re-queued on start. Cleanup of
`work/<job_id>` in a `finally`, on every path including cancellation. A startup
sweep. `/status` shows disk usage. Tested in `tests/test_worker.py`.

## 5. FSM state collided across concurrent requests

**What happened.** aiogram FSM states drove the choice flow, so sending a
second link while the first was mid-dialogue corrupted both. It was patched
with a global dict keyed by message id and `"type|audio|123"` string callbacks
parsed by `split('|')`.

**What we do.** Typed `CallbackData` carrying a `req_id` that resolves to a row
in `requests`. No FSM for the link flow. Several links can be in flight at
once. A callback for an unknown or expired request answers with a toast and
removes its keyboard.

## 6. Blocking calls and in-process yt-dlp

**What happened.** yt-dlp ran in-process through `run_in_executor`, and a
format listing ran *synchronously inside a callback handler*, freezing the
whole bot for seconds per click. A full `extract_info` network round-trip was
made just to read the video id out of the result.

**What we do.** yt-dlp and ffmpeg as async subprocesses with timeouts. The
video id comes from a regex. Nothing blocking in a handler.

## 7. Re-encoding every chapter

**What happened.** Each chapter was re-encoded with libmp3lame at 192k: slow,
and lossy on top of an already lossy source.

**What we do.** `-c copy`. Stream copy is fast and lossless; `-ss` before `-i`
seeks to the nearest frame, which for MP3 is about 26 ms.

## 8. Duplicate code paths

**What happened.** Two near-identical audio processing functions, one on an old
handler path and one in the worker, which drifted apart. A second "tag an
uploaded audio with a cover" feature existed half-wired and disabled.

**What we do.** One pipeline. A feature is in scope and tested, or it is not
present.

## 9. Metadata in the wrong tag

**What happened.** The download date and source URL were stored by overwriting
the album tag with `"<url> (<date>)"`, so every player showed a URL where the
album name belongs.

**What we do.** `TALB` is the video title. Provenance goes to `COMM`
(human-readable) and `TXXX:SOURCE_URL`, `TXXX:DOWNLOADED_AT`, `TXXX:VIDEO_ID`
(machine-readable). Asserted in `tests/test_tagging.py`.

## 10. Hygiene

**What happened.** `requirements.txt` was UTF-16 with a BOM, which breaks
`pip install -r` on Linux. A committed `runtime.txt` pinned an unrelated Python
version. The start script referenced a venv name that did not match
`.gitignore`. eyeD3 and mutagen were both in use. Paths were strings
everywhere. There was config for a MySQL database that was never used. Personal
music-library filenames and link lists were committed.

**What we do.** `pyproject.toml` and `uv.lock`, UTF-8 throughout, Docker as the
runtime. mutagen only. `pathlib.Path` only. No dead config. No personal data;
every test fixture is synthetic, and the audio ones are generated sine waves.
