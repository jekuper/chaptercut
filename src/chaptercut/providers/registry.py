"""The set of sites this deployment accepts.

Adding a site is: write a `Provider`, add it to `ALL_PROVIDERS`. Nothing else
in the system needs to know it exists.
"""

from __future__ import annotations

from collections.abc import Iterator

from chaptercut.providers.base import URL_CANDIDATE_RE, MediaRef, Provider
from chaptercut.providers.tiktok import TikTokProvider
from chaptercut.providers.youtube import YouTubeProvider

# Order matters only for tie-breaking a URL two providers both claim, which
# should not happen; it is also the order shown in the help text.
ALL_PROVIDERS: tuple[Provider, ...] = (YouTubeProvider(), TikTokProvider())


class UnknownProviderError(KeyError):
    pass


class ProviderRegistry:
    def __init__(self, providers: tuple[Provider, ...] = ALL_PROVIDERS) -> None:
        self._providers = providers
        self._by_name = {provider.name: provider for provider in providers}

    @classmethod
    def enabled(cls, names: list[str]) -> ProviderRegistry:
        """A registry limited to `names`; an empty list means everything."""
        if not names:
            return cls()
        wanted = [name.strip().lower() for name in names if name.strip()]
        known = {provider.name for provider in ALL_PROVIDERS}
        unknown = sorted(set(wanted) - known)
        if unknown:
            raise UnknownProviderError(f"unknown provider(s) {unknown}; known: {sorted(known)}")
        return cls(tuple(p for p in ALL_PROVIDERS if p.name in wanted))

    def __iter__(self) -> Iterator[Provider]:
        return iter(self._providers)

    @property
    def providers(self) -> tuple[Provider, ...]:
        return self._providers

    @property
    def names(self) -> list[str]:
        return [provider.name for provider in self._providers]

    @property
    def labels(self) -> list[str]:
        return [provider.label for provider in self._providers]

    def get(self, name: str) -> Provider:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise UnknownProviderError(f"no provider named {name!r}") from exc

    def find(self, name: str) -> Provider | None:
        return self._by_name.get(name)

    def match(self, candidate: str) -> MediaRef | None:
        """The first enabled provider that claims `candidate`."""
        for provider in self._providers:
            ref = provider.match(candidate)
            if ref is not None:
                return ref
        return None

    def find_refs(self, text: str) -> list[MediaRef]:
        """Every distinct link in `text`, in order of appearance.

        One pass over the text, so a message holding a YouTube and a TikTok
        link comes back in the order they were written.
        """
        found: list[MediaRef] = []
        seen: set[tuple[str, str]] = set()
        for candidate in URL_CANDIDATE_RE.findall(text):
            ref = self.match(candidate)
            if ref is None:
                continue
            key = (ref.provider, ref.media_id)
            if key not in seen:
                seen.add(key)
                found.append(ref)
        return found
