"""TGL (The Guestlist) Podcast CLI Tool"""

from .config import Settings, settings, paths, TGLPaths
from .models import Episode, TrackInfo, Track, parse_episode_id
from .cache import MetadataCache
from .search import SearchIndex
from .fetcher import PatreonPodcastFetcher
from .spotify import SpotifyManager, SpotifyState, SpotifyTrackCache, SpotifyPlaylist
from .transcribe import TranscriptionCache, transcribe_audio, format_timestamp
from .analysis import TrackAnalyzer, TrackAnalysis, TracksDatabase

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
    "SpotifyState",
    "SpotifyTrackCache",
    "SpotifyPlaylist",
    "TranscriptionCache",
    "transcribe_audio",
    "format_timestamp",
    "TrackAnalyzer",
    "TrackAnalysis",
    "TracksDatabase",
    "paths",
    "TGLPaths",
]
