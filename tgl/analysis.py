"""Track analysis and cross-episode tracking for TGL

This module provides analysis capabilities for tracks across episodes:
- Track appearance tracking (which episodes contain each track)
- Last.fm tags analysis
- Extensible for future data sources
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from rich.console import Console
from pydantic import BaseModel, Field
import requests

from .config import paths, Settings
from .models import Episode

console = Console()


class TrackAnalysis(BaseModel):
    """Analysis data for a single track"""
    episodes: List[str] = Field(default_factory=list, description="Episode GUIDs this track appears in")
    lastfm_tags: Optional[List[Dict[str, Any]]] = Field(default=None, description="Last.fm track tags")
    lastfm_tags_source: Optional[str] = Field(default=None, description="Source of tags: 'track' or 'artist'")

    def add_episode(self, guid: str):
        """Add an episode GUID if not already present"""
        if guid not in self.episodes:
            self.episodes.append(guid)


class TracksDatabase(BaseModel):
    """Database of all analyzed tracks"""
    tracks: Dict[str, TrackAnalysis] = Field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump(mode='json')

    @classmethod
    def from_dict(cls, data: Dict) -> 'TracksDatabase':
        """Create from dictionary"""
        tracks = {}
        for key, value in data.get('tracks', {}).items():
            if isinstance(value, dict):
                tracks[key] = TrackAnalysis(**value)
            else:
                tracks[key] = value
        return cls(tracks=tracks)


class TrackAnalyzer:
    """Manages track analysis across episodes"""

    def __init__(self, settings: Settings):
        """Initialize track analyzer

        Args:
            settings: Application settings with API credentials
        """
        self.settings = settings
        self.db_file = paths.data_dir / "tracks.json"
        self.db = self._load_db()

    def _load_db(self) -> TracksDatabase:
        """Load tracks database from tracks.json"""
        if self.db_file.exists():
            try:
                with open(self.db_file, 'r') as f:
                    data = json.load(f)
                return TracksDatabase.from_dict(data)
            except (json.JSONDecodeError, IOError) as e:
                console.print(f"[yellow]Warning: Could not load tracks database: {e}[/yellow]")
                return TracksDatabase()
        return TracksDatabase()

    def _save_db(self):
        """Save tracks database to tracks.json"""
        try:
            self.db_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.db_file, 'w') as f:
                json.dump(self.db.to_dict(), f, indent=2)
        except IOError as e:
            console.print(f"[red]Error saving tracks database: {e}[/red]")

    def _make_track_key(self, artist: str, title: str) -> str:
        """Create track key matching SpotifyManager format

        Args:
            artist: Track artist
            title: Track title

        Returns:
            Normalized track key (artist.lower()|title.lower())
        """
        return f"{artist.lower()}|{title.lower()}"

    def build_episode_mapping(self, episodes: List[Episode]) -> List[str]:
        """Build mapping of tracks to episodes they appear in

        Args:
            episodes: List of all episodes

        Returns:
            List of track keys that were added/updated in this run
        """
        console.print("\n[cyan]Building track-to-episode mapping...[/cyan]")

        track_count = 0
        episode_count = 0
        updated_track_keys = []

        for ep in episodes:
            if not ep.tracklist:
                continue

            episode_count += 1

            for track in ep.tracklist:
                track_key = self._make_track_key(track.artist, track.title)

                # Create track entry if it doesn't exist
                if track_key not in self.db.tracks:
                    self.db.tracks[track_key] = TrackAnalysis()
                    track_count += 1

                # Add episode to track's appearances
                self.db.tracks[track_key].add_episode(ep.guid)

                # Track this key as updated in this run
                if track_key not in updated_track_keys:
                    updated_track_keys.append(track_key)

        console.print(f"[green]✓[/green] Found {len(updated_track_keys)} unique tracks across {episode_count} episodes")
        self._save_db()

        return updated_track_keys

    def fetch_lastfm_tags(self, track_keys_filter: Optional[List[str]] = None):
        """Fetch Last.fm tags for tracks

        Args:
            track_keys_filter: Optional list of track keys to fetch tags for.
                              If None, fetches tags for all tracks in database.
        """
        console.print("\n[cyan]Fetching Last.fm tags...[/cyan]")

        if not self.settings.lastfm_api_key:
            console.print("[red]Error: Last.fm API key not configured[/red]")
            console.print("[dim]Set LASTFM_API_KEY in your .env file or config.toml[/dim]")
            return

        # Get tracks that need analysis
        tracks_to_analyze = []

        # Determine which tracks to process
        tracks_to_check = track_keys_filter if track_keys_filter else list(self.db.tracks.keys())

        for track_key in tracks_to_check:
            # Skip if track doesn't exist in database
            if track_key not in self.db.tracks:
                continue

            track_data = self.db.tracks[track_key]

            # Skip if we already have Last.fm tags (cache hit)
            if track_data.lastfm_tags is not None:
                continue

            # Parse artist and title from track key
            parts = track_key.split('|', 1)
            if len(parts) == 2:
                artist, title = parts
                tracks_to_analyze.append((track_key, artist, title))

        if not tracks_to_analyze:
            console.print(f"[yellow]All tracks already have Last.fm tags[/yellow]")
            return

        console.print(f"[dim]Tracks to fetch: {len(tracks_to_analyze)}[/dim]")

        analyzed_count = 0
        failed_count = 0
        rate_limit_delay = 0.25  # 250ms between requests (4 requests/sec, well below Last.fm's 5/sec limit)

        for i, (track_key, artist, title) in enumerate(tracks_to_analyze, 1):
            try:
                console.print(f"  [{i}/{len(tracks_to_analyze)}] [cyan]Fetching:[/cyan] {artist} - {title}")

                # Call Last.fm API
                url = "http://ws.audioscrobbler.com/2.0/"
                params = {
                    'method': 'track.getTopTags',
                    'artist': artist,
                    'track': title,
                    'api_key': self.settings.lastfm_api_key,
                    'autocorrect': 1,  # Enable autocorrection for better matching
                    'format': 'json'
                }

                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()

                data = response.json()

                # Extract tags from response
                tags = []
                tags_source = "track"
                if 'toptags' in data and 'tag' in data['toptags']:
                    tag_list = data['toptags']['tag']
                    # Ensure tag_list is a list (single tag returns dict)
                    if isinstance(tag_list, dict):
                        tag_list = [tag_list]

                    for tag in tag_list:
                        tags.append({
                            'name': tag.get('name', ''),
                            'count': int(tag.get('count', 0))
                        })

                # Fallback: if no track tags found, try artist tags
                if not tags:
                    console.print(f"    [yellow]⚠[/yellow] No track tags found, trying artist tags...")
                    time.sleep(rate_limit_delay)  # Rate limit before next request

                    try:
                        artist_params = {
                            'method': 'artist.getTopTags',
                            'artist': artist,
                            'api_key': self.settings.lastfm_api_key,
                            'autocorrect': 1,
                            'format': 'json'
                        }

                        artist_response = requests.get(url, params=artist_params, timeout=10)
                        artist_response.raise_for_status()
                        artist_data = artist_response.json()

                        if 'toptags' in artist_data and 'tag' in artist_data['toptags']:
                            artist_tag_list = artist_data['toptags']['tag']
                            if isinstance(artist_tag_list, dict):
                                artist_tag_list = [artist_tag_list]

                            for tag in artist_tag_list:
                                tags.append({
                                    'name': tag.get('name', ''),
                                    'count': int(tag.get('count', 0))
                                })

                            tags_source = "artist"

                    except Exception as artist_error:
                        console.print(f"    [red]✗[/red] Artist tags also failed: {artist_error}")

                # Store tags (empty list if no tags found)
                self.db.tracks[track_key].lastfm_tags = tags
                self.db.tracks[track_key].lastfm_tags_source = tags_source if tags else None
                analyzed_count += 1

                if tags:
                    tag_names = ', '.join([t['name'] for t in tags[:5]])
                    source_indicator = f" [dim](from {tags_source})[/dim]" if tags_source == "artist" else ""
                    console.print(f"    [green]✓[/green] Found {len(tags)} tags{source_indicator}: [dim]{tag_names}{'...' if len(tags) > 5 else ''}[/dim]")
                else:
                    console.print(f"    [yellow]⚠[/yellow] No tags found")

                # Save after every 10 tracks to avoid losing progress
                if analyzed_count % 10 == 0:
                    self._save_db()

                # Rate limiting
                time.sleep(rate_limit_delay)

            except requests.exceptions.RequestException as e:
                console.print(f"    [red]✗[/red] API error: {e}")
                # Store empty list to mark as attempted (cache the failure)
                self.db.tracks[track_key].lastfm_tags = []
                self.db.tracks[track_key].lastfm_tags_source = None
                failed_count += 1
            except Exception as e:
                console.print(f"    [red]✗[/red] Error: {e}")
                # Store empty list to mark as attempted
                self.db.tracks[track_key].lastfm_tags = []
                self.db.tracks[track_key].lastfm_tags_source = None
                failed_count += 1

        # Final save
        self._save_db()

        console.print(f"\n[green]✓[/green] Fetched tags for {analyzed_count} tracks")
        if failed_count:
            console.print(f"[yellow]⚠[/yellow] {failed_count} tracks could not be analyzed")

    def print_summary(self):
        """Print summary statistics"""
        console.print("\n[bold cyan]Track Analysis Summary[/bold cyan]\n")

        total_tracks = len(self.db.tracks)
        tracks_with_tags = sum(1 for t in self.db.tracks.values() if t.lastfm_tags is not None and len(t.lastfm_tags) > 0)
        tracks_attempted = sum(1 for t in self.db.tracks.values() if t.lastfm_tags is not None)
        tags_from_track = sum(1 for t in self.db.tracks.values() if t.lastfm_tags_source == "track")
        tags_from_artist = sum(1 for t in self.db.tracks.values() if t.lastfm_tags_source == "artist")

        # Calculate appearance statistics
        appearances = [len(t.episodes) for t in self.db.tracks.values()]
        max_appearances = max(appearances) if appearances else 0
        avg_appearances = sum(appearances) / len(appearances) if appearances else 0

        console.print(f"Total unique tracks: [bold]{total_tracks}[/bold]")
        console.print(f"Tracks with Last.fm tags: [bold]{tracks_with_tags}[/bold] ({tracks_with_tags/total_tracks*100:.1f}%)")
        if tags_from_track > 0:
            console.print(f"  • From track tags: [bold]{tags_from_track}[/bold]")
        if tags_from_artist > 0:
            console.print(f"  • From artist tags (fallback): [bold]{tags_from_artist}[/bold]")
        console.print(f"Tracks attempted: [bold]{tracks_attempted}[/bold] ({tracks_attempted/total_tracks*100:.1f}%)")
        console.print(f"Average appearances per track: [bold]{avg_appearances:.1f}[/bold]")
        console.print(f"Most appearances: [bold]{max_appearances}[/bold] episodes")

        # Find most frequently appearing tracks
        if total_tracks > 0:
            console.print("\n[bold cyan]Most Frequent Tracks:[/bold cyan]")
            frequent_tracks = sorted(
                [(key, len(data.episodes)) for key, data in self.db.tracks.items()],
                key=lambda x: x[1],
                reverse=True
            )[:10]

            for i, (track_key, count) in enumerate(frequent_tracks, 1):
                # Parse artist and title from key
                parts = track_key.split('|', 1)
                if len(parts) == 2:
                    artist, title = parts
                    console.print(f"  {i}. {artist} - {title} [dim]({count} episodes)[/dim]")

        # Aggregate artist appearances
        if total_tracks > 0:
            console.print("\n[bold cyan]Most Played Artists:[/bold cyan]")
            artist_appearances = {}
            for track_key, data in self.db.tracks.items():
                parts = track_key.split('|', 1)
                if len(parts) == 2:
                    artist = parts[0]
                    # Count total episode appearances for this artist
                    if artist not in artist_appearances:
                        artist_appearances[artist] = 0
                    artist_appearances[artist] += len(data.episodes)

            # Sort by total appearances and show top 10
            top_artists = sorted(
                artist_appearances.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]

            for i, (artist, count) in enumerate(top_artists, 1):
                console.print(f"  {i}. {artist} [dim]({count} appearances)[/dim]")

        # Aggregate tags/genres
        if tracks_with_tags > 0:
            console.print("\n[bold cyan]Most Common Genres/Tags:[/bold cyan]")
            tag_counts = {}
            for data in self.db.tracks.values():
                if data.lastfm_tags:
                    for tag in data.lastfm_tags:
                        tag_name = tag.get('name', '').lower()
                        if tag_name:
                            if tag_name not in tag_counts:
                                tag_counts[tag_name] = 0
                            tag_counts[tag_name] += 1

            # Sort by track count and show top 20
            top_tags = sorted(
                tag_counts.items(),
                key=lambda x: x[1],
                reverse=True
            )[:20]

            for i, (tag, count) in enumerate(top_tags, 1):
                console.print(f"  {i}. {tag} [dim]({count} tracks)[/dim]")

        console.print()
