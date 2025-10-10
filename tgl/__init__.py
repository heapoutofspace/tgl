"""TGL (The Guestlist) Podcast CLI Tool"""

from .config import Settings, settings, paths, TGLPaths
from .models import Episode, TrackInfo, Track, parse_episode_id
from .cache import MetadataCache
from .search import SearchIndex
from .fetcher import PatreonPodcastFetcher
from .spotify import SpotifyManager

__version__ = "0.1.0"

__all__ = [
    "Settings",
    "settings",
    "Episode",
    "TrackInfo",
    "Track",
    "parse_episode_id",
    "MetadataCache",
    "SearchIndex",
    "PatreonPodcastFetcher",
    "SpotifyManager",
    "paths",
    "TGLPaths",
]
