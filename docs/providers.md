# Providers

A provider is everything the system needs to know about one source site. It is
deliberately small, because almost nothing downstream is site-specific:
yt-dlp and ffmpeg do not care which site a URL points at.

Currently shipped: **YouTube**, **TikTok**.

## What a provider owns

```python
class Provider(ABC):
    name: str                      # slug, lowercase alphanumeric, used in cache keys
    label: str                     # display name
    supports_chapters: bool        # False means audio is always a single track
    cache_audio: bool              # whether processed audio is worth keeping
    ytdlp_args: tuple[str, ...]    # extra flags for every call to this site

    def match(self, candidate: str) -> MediaRef | None: ...
    def is_canonical_id(self, media_id: str) -> bool: ...
    def clean_title(self, title: str) -> str: ...
```

`match` is pure pattern matching and does no I/O. That is what lets intake
answer instantly instead of shelling out to yt-dlp just to learn an id, which
is what the predecessor did.

A `MediaRef` is what it produces:

```python
MediaRef(provider="tiktok", media_id="7123...", url="https://...", resolved=True)
```

`url` is canonical: rebuilt or stripped so tracking parameters, playlist ids
and mobile hostnames never reach the network, the logs, or the tags.

## Adding a site

1. Write the provider in `src/chaptercut/providers/<site>.py`.
2. Add it to `ALL_PROVIDERS` in `providers/registry.py`.
3. Add its URL forms to `tests/test_providers.py`.

That is the whole list. The queue, cache, pipeline, keyboards and commands are
already generic.

Two things are worth checking while writing one:

- **yt-dlp has to accept your canonical URL.** Its extractors are picky. Verify
  rather than assume:

  ```python
  from yt_dlp.extractor import gen_extractor_classes
  [ie.IE_NAME for ie in gen_extractor_classes() if ie.suitable(url)]
  ```

  TikTok is the cautionary example: yt-dlp rejects `m.tiktok.com`, the
  `/v/<id>` path, and `/photo/` posts, so the provider rewrites all of them
  onto `www.tiktok.com/@<user>/video/<id>`.

- **`name` must be lowercase alphanumeric.** It becomes the first half of a
  cache directory name (`youtube-dQw4w9WgXcQ`), and the split is on the first
  hyphen. The base class enforces this at class-definition time.

## Ids that are not in the URL

Some share links are pure redirects: `vm.tiktok.com/ZMhqAbCdE` tells you
nothing about which video it points at.

Rather than resolving these with an HTTP call during intake, the provider
returns `resolved=False` with the short code as a placeholder id. The pipeline
then:

1. skips the cache lookup, since there is no id to look one up by;
2. fetches metadata, which it was going to do anyway;
3. takes the real id from `info["id"]` and re-checks the cache under it;
4. commits the result under the real id.

So two different share links for the same video converge on one cache entry,
and intake stays free of network calls.

## Cache keys

Entries are keyed by `(provider, media_id)` on disk (`cache/youtube-dQw4...`)
and in the `cache_entries` table, whose primary key is composite for the same
reason: an id is only unique within one site.

Ids reaching the cache come from yt-dlp, not only from our own regexes, so
anything outside `[A-Za-z0-9_-]` is replaced by a digest instead of being
trusted as a path component.

## Per-site cookies

`YtdlpFactory` picks the cookie jar per provider:

1. `<data_dir>/cookies-<provider>.txt` if it exists;
2. otherwise `CC_COOKIES_FILE`.

So giving TikTok its own login is a file drop, with no config change. Cookie
jars are domain-scoped in the Netscape format, so yt-dlp only ever sends
cookies matching the host it is talking to.

`/cookies` reports one line per site, with size and age only.

## Enabling a subset

`CC_ENABLED_PROVIDERS=youtube` limits a deployment to one site. Empty (the
default) means every provider the build knows about. An unknown name fails at
startup with the list of known ones rather than silently ignoring the link.

## Site notes

### YouTube

Chapters supported, so an album upload becomes one tagged MP3 per chapter.
Canonical URLs are rebuilt from the id as `watch?v=<id>`, which drops playlist
ids and `si=` tracking tokens.

### TikTok

No chapters, ever, so audio is always a single track and no ZIP is built.
Captions are usually a sentence followed by a pile of hashtags, so
`clean_title` strips a trailing hashtag run: `sunset timelapse #fyp #viral`
becomes `sunset timelapse`, which is what lands in `TIT2`, `TALB` and the
filename. A caption that is nothing but hashtags is left alone, since the
alternative is an empty title.

Photo (slideshow) posts have no yt-dlp extractor, but share the id namespace
with videos, so they are rewritten onto the video path. That reaches the
backing audio, which is what an audio request wants; a video request for one
will fail with yt-dlp's own message.
