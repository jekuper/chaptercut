"""Every user-facing string. Nothing else in the bot builds message text."""

from __future__ import annotations

from html import escape

from chaptercut.util.timefmt import format_bytes, format_uptime


def esc(value: str) -> str:
    """Video titles arrive from YouTube and go into HTML-parsed messages."""
    return escape(value or "", quote=False)


PRIVATE = "This bot is private."

START = (
    "Send me a link.\n\n"
    "I can give you back the audio, split into one tagged MP3 per chapter, "
    "or the video at a quality you pick."
)

HELP = (
    "<b>chaptercut</b>\n\n"
    "Send a link and choose:\n"
    "- <b>Audio</b>: one MP3 per chapter, fully tagged with cover art. "
    "A video without chapters comes back as a single track.\n"
    "- <b>Video</b>: pick from the qualities that are actually available.\n\n"
    "<b>Sites</b>\n"
    "{sites}\n\n"
    "<b>Commands</b>\n"
    "/status - queue, current job, cache size, uptime\n"
    "/cancel - drop your queued jobs\n"
    "/help - this message"
)

HELP_ADMIN = (
    "\n/cache - inspect or purge the cache"
    "\n/files - inspect or flush the file server"
    "\n/cookies - cookie file status"
)

NOT_A_LINK = (
    "That is not a link I recognise.\n"
    "I can take links from: {sites}.\n"
    "Send /help for what I can do."
)

MULTIPLE_LINKS = "Found several links. Processing the first one; send the others separately."

CHOOSE_TYPE = "What do you want from <b>{title}</b>?"
CHOOSE_TYPE_PLAIN = "What do you want from this video?"
CHOOSE_QUALITY = "Pick a quality:"
CHOOSE_DESTINATION = "Where should it go?"

FETCHING_FORMATS = "Checking available qualities..."
NO_FORMATS = "No downloadable video formats were listed for this link."

REQUEST_EXPIRED = "Request expired, send the link again."

QUEUED = "Queued - position {position}"
QUEUED_NEXT = "Queued - next up"

CACHE_HIT = "Served from cache"

CANCELLED_NONE = "You have nothing queued."
CANCELLED = "Cancelled {count} queued job(s)."
CANCELLED_RUNNING_NOTE = "\nThe job already running was left alone; it will finish."

RATE_LIMITED = "Slow down a moment."

FAILED = "Failed: {reason}"
FAILED_BOT_CHECK = (
    "YouTube is asking for a signed-in session for this video.\n"
    "The cookie file needs refreshing on the host."
)
FAILED_TOO_LARGE = (
    "The result is {size}, over the {limit} send limit. "
    "Try the audio instead, or a lower video quality."
)
FAILED_TIMEOUT = "The download took too long and was stopped."

LINK_READY = "<b>{title}</b>\n{size} - expires in {hours}h\n{url}"
FELL_BACK_TO_SERVER = "Too big for Telegram ({size}), so here is a direct link instead."
SERVER_UNAVAILABLE = "The file server is not configured."

FILES_USAGE_LINE = "File server: {count} file(s), {size}, kept {hours}h"
FILES_EMPTY = "Nothing on the file server."
FILES_ENTRY = "{filename} - {size} - {token}"
FILES_PURGED_ALL = "Flushed {count} file(s) from the server."
FILES_PURGED = "Deleted {token}."
FILES_NOT_FOUND = "No such file: {token}"
FILES_USAGE_HELP = "Usage: /files | /files purge &lt;token&gt; | /files purge all"
FILES_ERROR = "File server error: {reason}"

ADMIN_ONLY = "That command is for admins."

COOKIES_MISSING = "No cookie file is configured or present."
COOKIES_STATUS = "Cookie file: {size}, last modified {age} ago."

CACHE_USAGE_LINE = "Cache: {count} video(s), {size}"
CACHE_NOT_CACHED = "Not cached: {video_id}"
CACHE_ENTRY = "<b>{title}</b>\n{provider}:{video_id} - {tracks} track(s), {size}\nDownloaded {date}"
CACHE_PURGED = "Purged {video_id}."
CACHE_PURGED_ALL = "Purged {count} cache entries."
CACHE_USAGE_HELP = (
    "Usage: /cache &lt;url, id, or provider:id&gt; | /cache purge &lt;id&gt; | /cache purge all"
)
CACHE_AMBIGUOUS = "That id is cached for several sites. Use one of: {keys}"

PHASE_LABELS = {
    "queued": "Queued",
    "fetch": "Reading metadata",
    "download": "Downloading",
    "split": "Splitting",
    "tag": "Tagging",
    "package": "Packaging",
    "upload": "Uploading",
    "done": "Done",
}

BUTTON_AUDIO = "Audio"
BUTTON_VIDEO = "Video"
BUTTON_BACK = "Back"
BUTTON_TELEGRAM = "Telegram"
BUTTON_SERVER = "Direct link"

QUALITY_LABEL = "{height}p - {ext} - ~{size}"
QUALITY_LABEL_NO_SIZE = "{height}p - {ext}"

RESULT_AUDIO_SINGLE = "<b>{title}</b>\n{uploader}"
RESULT_AUDIO_MULTI = "<b>{title}</b>\n{uploader} - {tracks} tracks, {size}"
RESULT_VIDEO = "<b>{title}</b>\n{uploader} - {size}"


def not_a_link(labels: list[str]) -> str:
    return NOT_A_LINK.format(sites=", ".join(labels) or "nothing right now")


def help_text(labels: list[str], is_admin: bool = False) -> str:
    body = HELP.format(sites=", ".join(labels) or "none configured")
    return body + (HELP_ADMIN if is_admin else "")


def status_text(
    *,
    queue_length: int,
    running: str | None,
    cache_count: int,
    cache_bytes: int,
    uptime_seconds: float,
    ytdlp_version: str,
    ffmpeg_ok: bool,
    providers: list[str],
    fileserver: str = "not configured",
) -> str:
    lines = [
        f"Queue: {queue_length} waiting",
        f"Running: {esc(running)}" if running else "Running: nothing",
        CACHE_USAGE_LINE.format(count=cache_count, size=format_bytes(cache_bytes)),
        f"Uptime: {format_uptime(uptime_seconds)}",
        f"yt-dlp: {ytdlp_version}",
        f"ffmpeg: {'ok' if ffmpeg_ok else 'MISSING'}",
        f"Sites: {', '.join(providers)}",
        f"File server: {esc(fileserver)}",
    ]
    return "\n".join(lines)


def progress_text(title: str, phase_label: str, pct: float | None, detail: str | None) -> str:
    head = f"<b>{esc(title)}</b>" if title else "Working"
    bar = _bar(pct)
    parts = [phase_label]
    if bar:
        parts.append(bar)
    if pct is not None:
        parts.append(f"{pct:.0f}%")
    if detail:
        parts.append(esc(detail))
    return f"{head}\n{'  '.join(parts)}"


def _bar(pct: float | None, width: int = 10) -> str:
    if pct is None:
        return ""
    filled = max(0, min(width, round(pct / 100 * width)))
    return "#" * filled + "." * (width - filled)


def queued_text(title: str, position: int) -> str:
    label = QUEUED_NEXT if position <= 1 else QUEUED.format(position=position)
    return f"<b>{title}</b>\n{label}" if title else label


def choose_type_text(title: str) -> str:
    return CHOOSE_TYPE.format(title=esc(title)) if title else CHOOSE_TYPE_PLAIN


def link_ready(title: str, url: str, size_bytes: int, hours: int) -> str:
    return LINK_READY.format(
        title=esc(title), size=format_bytes(size_bytes), hours=hours, url=esc(url)
    )


def files_entry(filename: str, size_bytes: int, token: str) -> str:
    return FILES_ENTRY.format(
        filename=esc(filename), size=format_bytes(size_bytes), token=esc(token)
    )


def quality_label(height: int, ext: str, size_bytes: int | None) -> str:
    if size_bytes:
        return QUALITY_LABEL.format(height=height, ext=ext, size=format_bytes(size_bytes))
    return QUALITY_LABEL_NO_SIZE.format(height=height, ext=ext)


def audio_caption(title: str, uploader: str, tracks: int, size_bytes: int) -> str:
    if tracks <= 1:
        return RESULT_AUDIO_SINGLE.format(title=esc(title), uploader=esc(uploader))
    return RESULT_AUDIO_MULTI.format(
        title=esc(title), uploader=esc(uploader), tracks=tracks, size=format_bytes(size_bytes)
    )


def video_caption(title: str, uploader: str, size_bytes: int) -> str:
    return RESULT_VIDEO.format(
        title=esc(title), uploader=esc(uploader), size=format_bytes(size_bytes)
    )


def cache_entry_text(
    title: str,
    provider: str,
    video_id: str,
    tracks: int,
    size_bytes: int,
    downloaded_at: str,
) -> str:
    return CACHE_ENTRY.format(
        title=esc(title),
        provider=provider,
        video_id=video_id,
        tracks=tracks,
        size=format_bytes(size_bytes),
        date=downloaded_at[:10] or "unknown",
    )
