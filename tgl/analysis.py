"""Track analysis and cross-episode tracking for TGL

This module provides analysis capabilities for tracks across episodes:
- Track appearance tracking (which episodes contain each track)
- Spotify audio features analysis
- Extensible for future data sources
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from rich.console import Console
from pydantic import BaseModel, Field

from .config import paths
from .models import Episode

console = Console()


class TrackAnalysis(BaseModel):
    """Analysis data for a single track"""
    episodes: List[str] = Field(default_factory=list, description="Episode GUIDs this track appears in")
    spotify_analysis: Optional[Dict[str, Any]] = Field(default=None, description="Spotify audio features")

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

    def __init__(self):
        """Initialize track analyzer"""
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

    def build_episode_mapping(self, episodes: List[Episode]):
        """Build mapping of tracks to episodes they appear in

        Args:
            episodes: List of all episodes
        """
        console.print("\n[cyan]Building track-to-episode mapping...[/cyan]")

        track_count = 0
        episode_count = 0

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

        console.print(f"[green]✓[/green] Found {len(self.db.tracks)} unique tracks across {episode_count} episodes")
        self._save_db()

    def analyze_spotify_features(self, spotify_manager):
        """Fetch Spotify audio features for all tracks

        Args:
            spotify_manager: SpotifyManager instance with track cache
        """
        console.print("\n[cyan]Analyzing Spotify audio features...[/cyan]")

        # Get tracks that need analysis
        tracks_to_analyze = []

        for track_key, track_data in self.db.tracks.items():
            # Skip if we already have analysis
            if track_data.spotify_analysis:
                continue

            # Check if we have a Spotify ID in the cache
            if track_key in spotify_manager.state.tracks:
                cached_track = spotify_manager.state.tracks[track_key]
                if cached_track.id:
                    tracks_to_analyze.append((track_key, cached_track.id))

        if not tracks_to_analyze:
            console.print(f"[yellow]No tracks need Spotify analysis[/yellow]")
            return

        console.print(f"[dim]Tracks to analyze: {len(tracks_to_analyze)}[/dim]")

        # Fetch audio features in batches of 100 (Spotify API limit)
        batch_size = 100
        analyzed_count = 0
        failed_count = 0

        try:
            client = spotify_manager._get_user_client()

            for i in range(0, len(tracks_to_analyze), batch_size):
                batch = tracks_to_analyze[i:i + batch_size]
                track_ids = [tid for _, tid in batch]

                console.print(f"[cyan]Fetching features for batch {i//batch_size + 1} ({len(track_ids)} tracks)...[/cyan]")

                try:
                    # Fetch audio features
                    spotify_manager._log_api_call("AUDIO_FEATURES", f"{len(track_ids)} tracks")
                    features_list = client.audio_features(track_ids)

                    # Store features for each track
                    for (track_key, track_id), features in zip(batch, features_list):
                        if features:
                            # Store the features (remove the track URI to save space)
                            features_clean = {k: v for k, v in features.items() if k not in ['uri', 'track_href', 'analysis_url']}
                            self.db.tracks[track_key].spotify_analysis = features_clean
                            analyzed_count += 1
                        else:
                            # Track exists but has no audio features (rare)
                            failed_count += 1

                    console.print(f"[green]✓[/green] Analyzed {len([f for f in features_list if f])} tracks")

                    # Save after each batch to avoid losing progress
                    self._save_db()

                except Exception as e:
                    console.print(f"[red]Error fetching features for batch: {e}[/red]")
                    failed_count += len(batch)

        except Exception as e:
            console.print(f"[red]Error initializing Spotify client: {e}[/red]")
            return

        console.print(f"\n[green]✓[/green] Analyzed {analyzed_count} tracks")
        if failed_count:
            console.print(f"[yellow]⚠[/yellow] {failed_count} tracks could not be analyzed")

    def print_summary(self):
        """Print summary statistics"""
        console.print("\n[bold cyan]Track Analysis Summary[/bold cyan]\n")

        total_tracks = len(self.db.tracks)
        tracks_with_analysis = sum(1 for t in self.db.tracks.values() if t.spotify_analysis)

        # Calculate appearance statistics
        appearances = [len(t.episodes) for t in self.db.tracks.values()]
        max_appearances = max(appearances) if appearances else 0
        avg_appearances = sum(appearances) / len(appearances) if appearances else 0

        console.print(f"Total unique tracks: [bold]{total_tracks}[/bold]")
        console.print(f"Tracks with Spotify analysis: [bold]{tracks_with_analysis}[/bold] ({tracks_with_analysis/total_tracks*100:.1f}%)")
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

        console.print()
