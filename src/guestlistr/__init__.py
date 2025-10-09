"""TGL (The Guestlist) Podcast CLI Tool"""

from .models import Settings, Episode, TrackInfo, Track, parse_episode_id
from .cache import MetadataCache
from .search import SearchIndex
from .fetcher import PatreonPodcastFetcher
from .state import StateManager
from .spotify import SpotifyPlaylistManager

__version__ = "0.1.0"

__all__ = [
    "Settings",
    "Episode",
    "TrackInfo",
    "Track",
    "parse_episode_id",
    "MetadataCache",
    "SearchIndex",
    "PatreonPodcastFetcher",
    "StateManager",
    "SpotifyPlaylistManager",
]
