"""State management for episode processing and failed track retries"""

import os
import json
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime
from rich.console import Console

from .models import Episode, Track
from .config import paths

console = Console()


class StateManager:
    """Manages persistent state for episode processing and failed track retries"""

    def __init__(self, state_file: Optional[Path] = None):
        # Use platform-specific state file by default
        self.state_file = state_file if state_file else paths.state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load state from file or return empty state"""
        if Path(self.state_file).exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                console.print(f"[yellow]Warning: Could not load state file: {e}[/yellow]")
                return self._empty_state()
        return self._empty_state()

    def _empty_state(self) -> Dict:
        """Return empty state structure"""
        return {
            "processed_episodes": {},
            "failed_tracks": {},
            "stats": {
                "last_run": None,
                "total_episodes_processed": 0,
                "total_tracks_found": 0
            }
        }

    def save(self):
        """Save current state to file"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except IOError as e:
            console.print(f"[red]Error saving state: {e}[/red]")

    def is_episode_processed(self, episode_link: str) -> bool:
        """Check if episode has already been processed"""
        return episode_link in self.state["processed_episodes"]

    def mark_episode_processed(self, episode: Episode, tracks_found: int):
        """Mark episode as processed"""
        self.state["processed_episodes"][episode.link] = {
            "title": episode.full_title,
            "processed_date": datetime.now().isoformat(),
            "tracks_found": tracks_found,
            "year": episode.year
        }
        self.state["stats"]["total_episodes_processed"] += 1
        self.state["stats"]["total_tracks_found"] += tracks_found
        self.state["stats"]["last_run"] = datetime.now().isoformat()

    def add_failed_track(self, track: Track, episode_title: str):
        """Add or update a failed track"""
        track_key = f"{track.artist} - {track.track}"

        if track_key in self.state["failed_tracks"]:
            self.state["failed_tracks"][track_key]["attempt_count"] += 1
            self.state["failed_tracks"][track_key]["last_attempt"] = datetime.now().isoformat()
        else:
            self.state["failed_tracks"][track_key] = {
                "artist": track.artist,
                "track": track.track,
                "source_episode": episode_title,
                "first_attempt": datetime.now().isoformat(),
                "last_attempt": datetime.now().isoformat(),
                "attempt_count": 1
            }

    def remove_failed_track(self, track_key: str):
        """Remove a track from failed tracks (found on Spotify)"""
        if track_key in self.state["failed_tracks"]:
            del self.state["failed_tracks"][track_key]

    def get_retryable_failed_tracks(self, max_attempts: int = 5, retry_after_days: int = 7) -> List[Dict]:
        """Get failed tracks that should be retried"""
        retryable = []
        now = datetime.now()

        for track_key, track_data in self.state["failed_tracks"].items():
            if track_data["attempt_count"] >= max_attempts:
                continue

            last_attempt = datetime.fromisoformat(track_data["last_attempt"])
            days_since_attempt = (now - last_attempt).days

            if days_since_attempt >= retry_after_days:
                retryable.append({
                    "key": track_key,
                    "artist": track_data["artist"],
                    "track": track_data["track"],
                    "query": f"{track_data['artist']} {track_data['track']}",
                    "attempt_count": track_data["attempt_count"]
                })

        return retryable
