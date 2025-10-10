"""Metadata cache manager for episode data"""

import json
from typing import Dict, List, Optional, TYPE_CHECKING
from pathlib import Path
from datetime import datetime
from rich.console import Console

from .models import Episode
from .config import paths

if TYPE_CHECKING:
    from .fetcher import PatreonPodcastFetcher
    from .search import SearchIndex

console = Console()


class MetadataCache:
    """Manages episode metadata cache with auto-refresh"""

    CACHE_MAX_AGE_HOURS = 1  # Auto-refresh if cache is older than this

    def __init__(self, cache_dir: Optional[Path] = None):
        # Use platform-specific data directory by default
        self.cache_dir = cache_dir if cache_dir else paths.data_dir
        self.metadata_file = paths.episodes_cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.episodes: Dict[int, Episode] = {}
        self.last_updated: Optional[datetime] = None
        self._load()

    def _load(self):
        """Load episodes from cache file"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    data = json.load(f)

                    # Load timestamp if present
                    if '_metadata' in data:
                        timestamp_str = data['_metadata'].get('last_updated')
                        if timestamp_str:
                            self.last_updated = datetime.fromisoformat(timestamp_str)

                    # Load episodes (skip metadata keys)
                    self.episodes = {
                        int(ep_id): Episode(**ep_data)
                        for ep_id, ep_data in data.items()
                        if not ep_id.startswith('_')
                    }

                age_str = ""
                if self.last_updated:
                    age = datetime.now() - self.last_updated
                    hours = age.total_seconds() / 3600
                    if hours < 1:
                        age_str = f" ({int(age.total_seconds() / 60)} minutes old)"
                    else:
                        age_str = f" ({hours:.1f} hours old)"

                console.print(f"[dim]Loaded {len(self.episodes)} episodes from cache{age_str}[/dim]")
            except (json.JSONDecodeError, IOError) as e:
                console.print(f"[yellow]Warning: Could not load cache: {e}[/yellow]")
                self.episodes = {}
                self.last_updated = None

    def save(self):
        """Save episodes to cache file with timestamp"""
        try:
            self.last_updated = datetime.now()
            data = {
                '_metadata': {
                    'last_updated': self.last_updated.isoformat(),
                    'episode_count': len(self.episodes)
                }
            }
            # Add episodes
            data.update({ep_id: ep.model_dump() for ep_id, ep in self.episodes.items()})

            with open(self.metadata_file, 'w') as f:
                json.dump(data, f, indent=2)
            console.print(f"[dim]Saved {len(self.episodes)} episodes to cache[/dim]")
        except IOError as e:
            console.print(f"[red]Error saving cache: {e}[/red]")

    def add_episode(self, episode: Episode):
        """Add or update an episode in the cache"""
        self.episodes[episode.id] = episode

    def get_episode(self, episode_id: int) -> Optional[Episode]:
        """Get an episode by ID"""
        return self.episodes.get(episode_id)

    def get_all_episodes(self) -> List[Episode]:
        """Get all episodes sorted by date descending"""
        return sorted(self.episodes.values(), key=lambda e: e.published, reverse=True)

    def get_episodes_by_year(self, year: int) -> List[Episode]:
        """Get episodes for a specific year, sorted by date descending"""
        year_episodes = [ep for ep in self.episodes.values() if ep.year == year]
        return sorted(year_episodes, key=lambda e: e.published, reverse=True)

    def get_available_years(self) -> List[int]:
        """Get list of available years"""
        years = set(ep.year for ep in self.episodes.values() if ep.year)
        return sorted(years, reverse=True)

    def is_stale(self) -> bool:
        """Check if cache is older than CACHE_MAX_AGE_HOURS"""
        if not self.last_updated:
            return True

        age = datetime.now() - self.last_updated
        return age.total_seconds() / 3600 > self.CACHE_MAX_AGE_HOURS

    def should_auto_refresh(self) -> bool:
        """Check if cache should be auto-refreshed (stale or empty)"""
        return not self.episodes or self.is_stale()

    def refresh(self, fetcher: 'PatreonPodcastFetcher'):
        """Refresh cache by fetching latest episodes

        Args:
            fetcher: PatreonPodcastFetcher instance to use for fetching
        """
        # Import here to avoid circular dependency
        from .search import SearchIndex

        console.print("[cyan]Refreshing episode cache...[/cyan]")
        episodes = fetcher.fetch_episodes()

        if episodes:
            for episode in episodes:
                self.add_episode(episode)
            self.save()
            console.print(f"[green]✓[/green] Cache refreshed with {len(episodes)} episodes")

            # Rebuild search index
            console.print("[cyan]Building search index...[/cyan]")
            search_index = SearchIndex(self.cache_dir)
            search_index.build_index(self.episodes)
            console.print(f"[green]✓[/green] Search index built\n")
        else:
            console.print("[yellow]Warning: No episodes fetched[/yellow]\n")
