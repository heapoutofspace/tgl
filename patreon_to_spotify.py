#!/usr/bin/env python3
# /// script
# dependencies = [
#   "feedparser==6.0.11",
#   "spotipy==2.24.0",
#   "python-dotenv==1.0.1",
#   "requests==2.31.0",
#   "rich==13.7.1",
# ]
# ///
"""
Patreon Podcast to Spotify Playlist
Extracts tracklists from Patreon podcast episodes and creates a Spotify playlist
"""

import os
import re
import json
import argparse
from typing import List, Dict, Optional
from html import unescape
from datetime import datetime
import time
import feedparser
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table

# Load environment variables
load_dotenv()

# Initialize console
console = Console()


class PatreonPodcastFetcher:
    """Fetches podcast episodes from Patreon RSS feed"""

    def __init__(self, rss_url: str):
        self.rss_url = rss_url

    def fetch_episodes(self, limit: Optional[int] = None) -> List[Dict]:
        """Fetch episodes from the RSS feed

        Args:
            limit: Number of recent episodes to fetch. If None, fetch all episodes.
        """
        try:
            # Fetch the feed content using requests (better SSL handling)
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; PatreonToSpotify/1.0)'
            }
            response = requests.get(self.rss_url, headers=headers, timeout=30)
            response.raise_for_status()

            # Parse the feed content
            feed = feedparser.parse(response.content)

            # Check for parsing errors
            if feed.bozo:
                console.print(f"[yellow]Warning: Feed parsing encountered an issue: {feed.bozo_exception}[/yellow]")

            episodes = []
            entries_to_process = feed.entries if limit is None else feed.entries[:limit]

            for entry in entries_to_process:
                # Parse published date to get year
                published_parsed = entry.get('published_parsed')
                year = None
                if published_parsed:
                    year = published_parsed.tm_year

                episode = {
                    'title': entry.get('title', ''),
                    'description': entry.get('description', '') or entry.get('summary', ''),
                    'published': entry.get('published', ''),
                    'year': year,
                    'link': entry.get('link', '')
                }
                episodes.append(episode)

            return episodes

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error fetching RSS feed: {e}[/red]")
            return []

    def get_available_years(self) -> List[int]:
        """Get list of available years from all episodes"""
        episodes = self.fetch_episodes()
        years = set()
        for episode in episodes:
            if episode['year']:
                years.add(episode['year'])
        return sorted(years, reverse=True)

    def filter_by_year(self, episodes: List[Dict], year: int) -> List[Dict]:
        """Filter episodes by year"""
        return [ep for ep in episodes if ep.get('year') == year]


class TracklistParser:
    """Parses tracklists from episode show notes"""

    def __init__(self):
        pass

    def _strip_html(self, html_text: str) -> str:
        """Strip HTML tags and unescape HTML entities"""
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '\n', html_text)
        # Unescape HTML entities (&amp; -> &, etc.)
        text = unescape(text)
        return text

    def parse_tracklist(self, description: str) -> List[Dict[str, str]]:
        """Extract tracks from episode description"""
        # Strip HTML tags and entities
        clean_text = self._strip_html(description)

        tracks = []
        seen = set()  # To avoid duplicates

        # Split into lines and process each
        for line in clean_text.split('\n'):
            line = line.strip()

            # Skip empty lines or lines that are too short
            if not line or len(line) < 5:
                continue

            # Skip lines that look like headers/sections
            if any(marker in line.lower() for marker in ['tracklist', 'record of the week', 'from the crates', 'also recommended', 'guestmix']):
                continue

            # Remove leading # or numbers
            line = re.sub(r'^[#\d\.\)]+\s*', '', line).strip()

            # Try to parse "Artist - Track" format
            # Match: Artist - Track (with optional extras in parentheses)
            match = re.match(r'^(.+?)\s*[-–—]\s*(.+?)(?:\s*\([^\)]*\))?\s*$', line)

            if match:
                artist = match.group(1).strip()
                track = match.group(2).strip()

                # Clean up track name
                # Remove "feat.", "ft.", "(Original Mix)", etc. from track name if they're at the end
                track = re.sub(r'\s*\(Original Mix\)\s*$', '', track, flags=re.IGNORECASE)

                # Skip if artist or track is too short
                if len(artist) < 2 or len(track) < 2:
                    continue

                # Skip if looks like metadata (contains URLs, etc.)
                if 'http' in line.lower() or 'www.' in line.lower():
                    continue

                # Create unique key to avoid duplicates
                track_key = f"{artist.lower()}|{track.lower()}"
                if track_key not in seen:
                    seen.add(track_key)
                    tracks.append({
                        'artist': artist,
                        'track': track,
                        'query': f"{artist} {track}"
                    })

        return tracks


class SpotifyPlaylistManager:
    """Manages Spotify playlist creation and track additions"""

    def __init__(self):
        scope = "playlist-modify-public playlist-modify-private"
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=os.getenv('SPOTIFY_CLIENT_ID'),
            client_secret=os.getenv('SPOTIFY_CLIENT_SECRET'),
            redirect_uri=os.getenv('SPOTIFY_REDIRECT_URI', 'http://localhost:8888/callback'),
            scope=scope
        ))
        self.user_id = self.sp.current_user()['id']

    def search_track(self, track_info: Dict[str, str]) -> Optional[str]:
        """Search for a track on Spotify and return the URI"""
        query = track_info['query']

        try:
            results = self.sp.search(q=query, type='track', limit=1)

            if results['tracks']['items']:
                track = results['tracks']['items'][0]
                return track['uri']
            else:
                return None
        except Exception as e:
            return None

    def create_playlist(self, name: str, description: str = "") -> str:
        """Create a new playlist and return its ID"""
        playlist = self.sp.user_playlist_create(
            user=self.user_id,
            name=name,
            public=True,
            description=description
        )
        return playlist['id']

    def get_playlist_by_name(self, name: str) -> Optional[str]:
        """Find a playlist by name and return its ID"""
        playlists = self.sp.current_user_playlists()

        for playlist in playlists['items']:
            if playlist['name'] == name:
                return playlist['id']

        return None

    def add_tracks_to_playlist(self, playlist_id: str, track_uris: List[str]) -> int:
        """Add tracks to a playlist (in batches of 100)

        Returns:
            Number of tracks added
        """
        # Remove duplicates while preserving order
        seen = set()
        unique_uris = []
        for uri in track_uris:
            if uri not in seen:
                seen.add(uri)
                unique_uris.append(uri)

        # Add tracks in batches of 100 (Spotify API limit)
        batch_size = 100
        for i in range(0, len(unique_uris), batch_size):
            batch = unique_uris[i:i + batch_size]
            self.sp.playlist_add_items(playlist_id, batch)

        return len(unique_uris)

    def get_playlist_tracks(self, playlist_id: str) -> List[str]:
        """Get all track URIs currently in a playlist"""
        tracks = []
        results = self.sp.playlist_items(playlist_id)

        while results:
            for item in results['items']:
                if item['track']:
                    tracks.append(item['track']['uri'])

            if results['next']:
                results = self.sp.next(results)
            else:
                break

        return tracks


class StateManager:
    """Manages persistent state for episode processing and failed track retries"""

    def __init__(self, state_file: str = ".guestlistr_state.json"):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load state from file or return empty state"""
        if os.path.exists(self.state_file):
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

    def mark_episode_processed(self, episode: Dict, tracks_found: int):
        """Mark episode as processed"""
        self.state["processed_episodes"][episode['link']] = {
            "title": episode['title'],
            "processed_date": datetime.now().isoformat(),
            "tracks_found": tracks_found,
            "year": episode.get('year')
        }
        self.state["stats"]["total_episodes_processed"] += 1
        self.state["stats"]["total_tracks_found"] += tracks_found
        self.state["stats"]["last_run"] = datetime.now().isoformat()

    def add_failed_track(self, track: Dict[str, str], episode_title: str):
        """Add or update a failed track"""
        track_key = f"{track['artist']} - {track['track']}"

        if track_key in self.state["failed_tracks"]:
            # Update existing failed track
            self.state["failed_tracks"][track_key]["attempt_count"] += 1
            self.state["failed_tracks"][track_key]["last_attempt"] = datetime.now().isoformat()
        else:
            # New failed track
            self.state["failed_tracks"][track_key] = {
                "artist": track['artist'],
                "track": track['track'],
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
            # Skip if exceeded max attempts
            if track_data["attempt_count"] >= max_attempts:
                continue

            # Check if enough time has passed since last attempt
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

    def clean_old_failures(self, max_attempts: int = 5) -> int:
        """Remove failed tracks that have exceeded max attempts"""
        to_remove = [
            key for key, data in self.state["failed_tracks"].items()
            if data["attempt_count"] >= max_attempts
        ]

        for key in to_remove:
            del self.state["failed_tracks"][key]

        return len(to_remove)

    def get_stats(self) -> Dict:
        """Get statistics about the cache"""
        failed_count = len(self.state["failed_tracks"])
        retryable_count = len(self.get_retryable_failed_tracks())
        maxed_out_count = sum(
            1 for data in self.state["failed_tracks"].values()
            if data["attempt_count"] >= 5
        )

        return {
            "processed_episodes": len(self.state["processed_episodes"]),
            "failed_tracks_total": failed_count,
            "failed_tracks_retryable": retryable_count,
            "failed_tracks_maxed_out": maxed_out_count,
            "total_episodes_processed": self.state["stats"]["total_episodes_processed"],
            "total_tracks_found": self.state["stats"]["total_tracks_found"],
            "last_run": self.state["stats"]["last_run"]
        }


def main():
    """Main execution function"""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Extract tracklists from Patreon podcast episodes and create a Spotify playlist"
    )
    parser.add_argument(
        "-n",
        "--episodes",
        type=int,
        default=None,
        help="Number of recent episodes to process (default: all episodes)"
    )
    parser.add_argument(
        "--dryrun",
        action="store_true",
        help="Dry run mode: parse episodes and search tracks without creating/updating Spotify playlist"
    )
    parser.add_argument(
        "--years",
        action="store_true",
        help="List all available years from podcast episodes and exit"
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Filter episodes by specific year (e.g., 2024)"
    )
    parser.add_argument(
        "--show-cache",
        action="store_true",
        help="Show cache statistics and exit"
    )
    parser.add_argument(
        "--clean-cache",
        action="store_true",
        help="Remove failed tracks that have exceeded max retry attempts"
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore cache and reprocess all episodes"
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Use cache even when filtering by year (by default --year skips cache)"
    )
    parser.add_argument(
        "--per-episode",
        action="store_true",
        help="Create individual playlists for each episode instead of one combined playlist"
    )
    parser.add_argument(
        "--playlist-prefix",
        type=str,
        default="",
        help="Prefix for playlist names in per-episode mode (e.g., 'TGL - ' results in 'TGL - Episode Title')"
    )
    parser.add_argument(
        "--episode-num",
        type=int,
        default=None,
        help="Process a specific episode number (e.g., 390 for 'E390'). Bypasses cache for that episode."
    )
    args = parser.parse_args()

    episodes_limit = args.episodes
    dryrun = args.dryrun
    list_years = args.years
    filter_year = args.year
    show_cache = args.show_cache
    clean_cache = args.clean_cache
    force_refresh = args.force_refresh
    use_cache = args.use_cache
    per_episode = args.per_episode
    playlist_prefix = args.playlist_prefix
    episode_num = args.episode_num

    console.print("\n[bold cyan]" + "═" * 60)
    console.print("[bold cyan]Patreon Podcast to Spotify Playlist")
    if dryrun:
        console.print("[bold yellow]🔍 DRY RUN MODE[/bold yellow]")
    console.print("[bold cyan]" + "═" * 60 + "\n")

    # Configuration
    RSS_URL = os.getenv('PATREON_RSS_URL')
    BASE_PLAYLIST_NAME = os.getenv('SPOTIFY_PLAYLIST_NAME', 'guestlistr')

    # Add year to playlist name if filtering by year
    if filter_year:
        PLAYLIST_NAME = f"{BASE_PLAYLIST_NAME} {filter_year}"
    else:
        PLAYLIST_NAME = BASE_PLAYLIST_NAME

    if not RSS_URL:
        console.print("[red]Error: PATREON_RSS_URL not set in .env file[/red]")
        raise SystemExit(1)

    # Initialize components
    fetcher = PatreonPodcastFetcher(RSS_URL)
    parser = TracklistParser()

    # Use separate state file for dryrun mode
    state_file = ".guestlistr_state_dryrun.json" if dryrun else ".guestlistr_state.json"
    state = StateManager(state_file)

    # Handle --show-cache flag (show cache stats and exit)
    if show_cache:
        stats = state.get_stats()

        table = Table(title="Cache Statistics", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green", justify="right")

        table.add_row("Processed Episodes", str(stats["processed_episodes"]))
        table.add_row("Total Episodes Processed (all time)", str(stats["total_episodes_processed"]))
        table.add_row("Total Tracks Found (all time)", str(stats["total_tracks_found"]))
        table.add_row("Failed Tracks (total)", str(stats["failed_tracks_total"]))
        table.add_row("Failed Tracks (retryable)", str(stats["failed_tracks_retryable"]))
        table.add_row("Failed Tracks (maxed out)", str(stats["failed_tracks_maxed_out"]))

        if stats["last_run"]:
            last_run_dt = datetime.fromisoformat(stats["last_run"])
            table.add_row("Last Run", last_run_dt.strftime("%Y-%m-%d %H:%M:%S"))
        else:
            table.add_row("Last Run", "Never")

        console.print(table)
        console.print(f"\n[dim]Cache file: {state.state_file}[/dim]\n")
        return

    # Handle --clean-cache flag (clean old failures and exit)
    if clean_cache:
        console.print("[cyan]Cleaning old failed tracks...[/cyan]")
        removed = state.clean_old_failures()
        state.save()
        console.print(f"[green]✓[/green] Removed {removed} failed tracks that exceeded max retry attempts\n")
        return

    # Handle --years flag (list years and exit)
    if list_years:
        console.print("[cyan]Fetching episodes to analyze years...[/cyan]\n")
        years = fetcher.get_available_years()

        if not years:
            console.print("[yellow]No years found in episodes[/yellow]")
            return

        # Create a nice table
        table = Table(title="Available Years", show_header=True, header_style="bold cyan")
        table.add_column("Year", style="green", justify="center")
        table.add_column("Episodes", style="yellow", justify="center")

        # Count episodes per year
        all_episodes = fetcher.fetch_episodes()
        year_counts = {}
        for episode in all_episodes:
            year = episode.get('year')
            if year:
                year_counts[year] = year_counts.get(year, 0) + 1

        for year in years:
            table.add_row(str(year), str(year_counts.get(year, 0)))

        console.print(table)
        console.print(f"\n[dim]Use --year YYYY to filter episodes by year[/dim]\n")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:

        # Fetch episodes
        fetch_task = progress.add_task("[cyan]Fetching episodes from RSS feed...", total=None)

        # If filtering by episode number or year, fetch all episodes first
        if episode_num or filter_year:
            fetched_episodes = fetcher.fetch_episodes(limit=None)
            progress.update(fetch_task, completed=True, total=1)

            if not fetched_episodes:
                console.print("[red]No episodes found![/red]")
                raise SystemExit(1)

            original_count = len(fetched_episodes)

            # Filter by episode number if specified
            if episode_num:
                # Match episode number in title (e.g., "E390", "e390", "Episode 390")
                import re
                episode_pattern = re.compile(rf'\be{episode_num}\b|\bepisode\s+{episode_num}\b', re.IGNORECASE)
                fetched_episodes = [ep for ep in fetched_episodes if episode_pattern.search(ep['title'])]

                if not fetched_episodes:
                    console.print(f"[red]No episode found matching number {episode_num}![/red]")
                    raise SystemExit(1)

                console.print(f"[green]✓[/green] Found episode {episode_num}: {fetched_episodes[0]['title']}\n")

            # Filter by year if specified
            elif filter_year:
                fetched_episodes = fetcher.filter_by_year(fetched_episodes, filter_year)

                if not fetched_episodes:
                    console.print(f"[red]No episodes found for year {filter_year}![/red]")
                    raise SystemExit(1)

                # Apply limit after filtering if specified
                if episodes_limit:
                    fetched_episodes = fetched_episodes[:episodes_limit]
                    console.print(f"[green]✓[/green] Found {len(fetched_episodes)} episodes for year {filter_year} (limited to {episodes_limit}, out of {original_count} total)\n")
                else:
                    console.print(f"[green]✓[/green] Found {len(fetched_episodes)} episodes for year {filter_year} (out of {original_count} total)\n")
        else:
            fetched_episodes = fetcher.fetch_episodes(limit=episodes_limit)
            progress.update(fetch_task, completed=True, total=1)

            if not fetched_episodes:
                console.print("[red]No episodes found![/red]")
                raise SystemExit(1)

            episodes_desc = f"last {episodes_limit}" if episodes_limit else "all"
            console.print(f"[green]✓[/green] Found {len(fetched_episodes)} episodes ({episodes_desc})\n")

        # Filter out already-processed episodes
        # Skip cache if: force_refresh OR episode_num OR (filtering by year AND not use_cache)
        should_use_cache = not force_refresh and not episode_num and (not filter_year or use_cache)

        if should_use_cache:
            new_episodes = [ep for ep in fetched_episodes if not state.is_episode_processed(ep['link'])]
            cached_count = len(fetched_episodes) - len(new_episodes)

            if cached_count > 0:
                console.print(f"[cyan]ℹ[/cyan] Skipping {cached_count} already-processed episodes (use --force-refresh to reprocess)\n")

            fetched_episodes = new_episodes

        if not fetched_episodes and not dryrun:
            console.print("[yellow]No new episodes to process[/yellow]\n")
            # Still check for retryable failed tracks
        elif fetched_episodes:
            console.print(f"[cyan]Processing {len(fetched_episodes)} new episodes...[/cyan]\n")

        # Parse tracklists from new episodes
        all_tracks = []
        episode_tracks = {}  # Track which episode each track came from

        if fetched_episodes:
            parse_task = progress.add_task("[cyan]Parsing tracklists...", total=len(fetched_episodes))

            for episode in fetched_episodes:
                tracks = parser.parse_tracklist(episode['description'])
                all_tracks.extend(tracks)
                episode_tracks[episode['link']] = tracks
                progress.update(parse_task, advance=1)

            console.print(f"[green]✓[/green] Extracted {len(all_tracks)} tracks from {len(fetched_episodes)} new episodes\n")

        if dryrun:
            # Dry run mode - skip Spotify operations
            console.print("[yellow]⚠ Dry run mode - skipping Spotify operations[/yellow]\n")
            console.print(f"[cyan]Summary:[/cyan]")
            console.print(f"  • Episodes processed: {len(fetched_episodes)}")
            console.print(f"  • Total tracks extracted: {len(all_tracks)}")
            console.print(f"\n[dim]Tracks found:[/dim]")
            for i, track in enumerate(all_tracks[:10], 1):  # Show first 10
                console.print(f"  {i}. {track['artist']} - {track['track']}")
            if len(all_tracks) > 10:
                console.print(f"  ... and {len(all_tracks) - 10} more")
        else:
            # Initialize Spotify (may require browser auth)
            console.print("[cyan]Initializing Spotify connection...[/cyan]")
            spotify = SpotifyPlaylistManager()
            console.print("[green]✓[/green] Connected to Spotify\n")

            # Get retryable failed tracks
            retryable_tracks = state.get_retryable_failed_tracks()

            if retryable_tracks:
                console.print(f"[cyan]ℹ[/cyan] Found {len(retryable_tracks)} previously failed tracks to retry\n")

            if per_episode:
                # Per-episode mode: Create individual playlists for each episode
                console.print("[cyan]Per-episode mode: Creating individual playlists[/cyan]\n")

                total_tracks_to_search = len(all_tracks) + len(retryable_tracks)

                if total_tracks_to_search > 0:
                    search_task = progress.add_task("[cyan]Processing episodes...", total=total_tracks_to_search)

                    playlists_created = 0
                    playlists_updated = 0
                    total_tracks_added = 0

                    # Process each episode
                    for episode in fetched_episodes:
                        ep_tracks = episode_tracks.get(episode['link'], [])
                        ep_track_uris = []

                        # Search tracks for this episode
                        for track in ep_tracks:
                            uri = spotify.search_track(track)
                            if uri:
                                ep_track_uris.append(uri)
                            else:
                                state.add_failed_track(track, episode['title'])
                            progress.update(search_task, advance=1)

                        # Create or update playlist for this episode
                        if ep_track_uris:
                            ep_playlist_name = f"{playlist_prefix}{episode['title']}"
                            ep_playlist_id = spotify.get_playlist_by_name(ep_playlist_name)

                            if ep_playlist_id:
                                # Update existing playlist
                                existing_tracks = spotify.get_playlist_tracks(ep_playlist_id)
                                new_tracks = [uri for uri in ep_track_uris if uri not in existing_tracks]

                                if new_tracks:
                                    spotify.add_tracks_to_playlist(ep_playlist_id, new_tracks)
                                    total_tracks_added += len(new_tracks)
                                    playlists_updated += 1
                            else:
                                # Create new playlist
                                ep_playlist_id = spotify.create_playlist(
                                    name=ep_playlist_name,
                                    description=f"Tracks from {episode['title']}"
                                )
                                spotify.add_tracks_to_playlist(ep_playlist_id, ep_track_uris)
                                total_tracks_added += len(ep_track_uris)
                                playlists_created += 1

                        # Mark episode as processed and save state
                        state.mark_episode_processed(episode, len(ep_tracks))
                        state.save()

                    # Retry previously failed tracks (add to appropriate episode playlists if needed)
                    retry_found = 0
                    for track in retryable_tracks:
                        uri = spotify.search_track(track)
                        if uri:
                            state.remove_failed_track(track['key'])
                            retry_found += 1
                        else:
                            state.add_failed_track(track, "retry")
                        progress.update(search_task, advance=1)

                    if retryable_tracks:
                        state.save()

                    if retry_found > 0:
                        console.print(f"[green]✓[/green] Found {retry_found} previously failed tracks on Spotify!\n")

                    console.print(f"[green]✓[/green] Created {playlists_created} new playlists")
                    if playlists_updated > 0:
                        console.print(f"[green]✓[/green] Updated {playlists_updated} existing playlists")
                    console.print(f"[green]✓[/green] Added {total_tracks_added} total tracks\n")
            else:
                # Combined mode: Create one playlist with all tracks
                total_tracks_to_search = len(all_tracks) + len(retryable_tracks)

                track_uris = []
                failed_tracks = []

                if total_tracks_to_search > 0:
                    search_task = progress.add_task("[cyan]Searching tracks on Spotify...", total=total_tracks_to_search)

                    # Search new tracks from episodes (with incremental state saving)
                    for episode in fetched_episodes:
                        ep_tracks = episode_tracks.get(episode['link'], [])

                        for track in ep_tracks:
                            uri = spotify.search_track(track)
                            if uri:
                                track_uris.append(uri)
                            else:
                                # Track not found - will be marked as failed
                                failed_tracks.append(track)
                                state.add_failed_track(track, episode['title'])
                            progress.update(search_task, advance=1)

                        # Mark episode as processed and save state incrementally
                        state.mark_episode_processed(episode, len(ep_tracks))
                        state.save()

                    # Retry previously failed tracks
                    retry_found = 0
                    for track in retryable_tracks:
                        uri = spotify.search_track(track)
                        if uri:
                            track_uris.append(uri)
                            state.remove_failed_track(track['key'])
                            retry_found += 1
                        else:
                            # Still not found, increment attempt count
                            state.add_failed_track(track, "retry")
                        progress.update(search_task, advance=1)

                    # Save state after processing retries
                    if retryable_tracks:
                        state.save()

                    if retry_found > 0:
                        console.print(f"[green]✓[/green] Found {retry_found} previously failed tracks on Spotify!\n")

                    console.print(f"[green]✓[/green] Found {len(track_uris)}/{total_tracks_to_search} tracks on Spotify\n")

                    if failed_tracks:
                        console.print(f"[yellow]⚠[/yellow] {len(failed_tracks)} tracks not found (will retry on next run)\n")

                # Create or update playlist
                playlist_id = spotify.get_playlist_by_name(PLAYLIST_NAME)

                if playlist_id:
                    console.print(f"[cyan]Found existing playlist:[/cyan] {PLAYLIST_NAME}")
                    # Get existing tracks to avoid duplicates
                    existing_tracks = spotify.get_playlist_tracks(playlist_id)
                    new_tracks = [uri for uri in track_uris if uri not in existing_tracks]

                    if new_tracks:
                        console.print(f"[cyan]Adding {len(new_tracks)} new tracks to playlist...[/cyan]")
                        num_added = spotify.add_tracks_to_playlist(playlist_id, new_tracks)
                        console.print(f"[green]✓[/green] Added {num_added} tracks to playlist")
                    else:
                        console.print("[yellow]No new tracks to add[/yellow]")
                else:
                    console.print(f"[cyan]Creating new playlist:[/cyan] {PLAYLIST_NAME}")
                    playlist_id = spotify.create_playlist(
                        name=PLAYLIST_NAME,
                        description="Tracks from Patreon DJ mixes"
                    )
                    num_added = spotify.add_tracks_to_playlist(playlist_id, track_uris)
                    console.print(f"[green]✓[/green] Created playlist and added {num_added} tracks")

                # Get playlist URL
                playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
                console.print(f"\n[bold green]✓ Done![/bold green] Playlist URL: [link={playlist_url}]{playlist_url}[/link]")

    console.print("[bold cyan]" + "═" * 60 + "\n")


if __name__ == "__main__":
    main()
