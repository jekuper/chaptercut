"""Source sites. Adding one means adding a Provider, nothing more."""

from chaptercut.providers.base import MediaRef, Provider
from chaptercut.providers.registry import (
    ALL_PROVIDERS,
    ProviderRegistry,
    UnknownProviderError,
)

__all__ = [
    "ALL_PROVIDERS",
    "MediaRef",
    "Provider",
    "ProviderRegistry",
    "UnknownProviderError",
]
