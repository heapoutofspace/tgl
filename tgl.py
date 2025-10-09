#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "feedparser==6.0.11",
#   "spotipy==2.24.0",
#   "requests==2.31.0",
#   "rich==13.7.1",
#   "typer==0.15.3",
#   "click==8.1.7",
#   "pydantic==2.10.5",
#   "pydantic-settings==2.6.1",
#   "whoosh==2.7.4",
# ]
# ///
"""
TGL (The Guestlist) Podcast CLI Tool
Manage episodes, tracklists, and Spotify playlists
"""

import os
import re
import json
import typer
from typing import List, Dict, Optional, Annotated
from pathlib import Path
from html import unescape
from datetime import datetime
import time
import feedparser
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text
from pydantic import BaseModel, Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict
from whoosh import index
from whoosh.fields import Schema, ID, TEXT, STORED
from whoosh.qparser import MultifieldParser, OrGroup
from whoosh.writing import AsyncWriter

# Initialize console and app
console = Console()
app = typer.Typer(
    help="TGL (The Guestlist) Podcast CLI Tool",
    no_args_is_help=False,
    invoke_without_command=True
)


@app.callback()
def main(ctx: typer.Context):
    """TGL (The Guestlist) Podcast CLI Tool"""
    if ctx.invoked_subcommand is None:
        # No command provided, show custom help
        console.print("\n[bold cyan]" + "═" * 70)
        console.print("[bold cyan]TGL - The Guestlist Podcast CLI Tool")
        console.print("[bold cyan]" + "═" * 70 + "\n")

        console.print("[bold]Available Commands:[/bold]\n")

        console.print("  [cyan]list[/cyan]                 List all episodes")
        console.print("  [cyan]info[/cyan]                 Show details for a specific episode")
        console.print("  [cyan]search[/cyan]               Search episodes by title, description, or tracks")
        console.print("  [cyan]download[/cyan]             Download an episode audio file")
        console.print("  [cyan]spotify[/cyan]              Import tracklists to Spotify playlist")
        console.print("  [cyan]refresh[/cyan]              Refresh episode metadata from RSS feed\n")

        console.print("[bold]Examples:[/bold]\n")
        console.print("  [dim]# List all episodes[/dim]")
        console.print("  [green]tgl.py list[/green]\n")

        console.print("  [dim]# List only TGL episodes from 2023[/dim]")
        console.print("  [green]tgl.py list --year 2023 --tgl[/green]\n")

        console.print("  [dim]# List only BONUS episodes[/dim]")
        console.print("  [green]tgl.py list --bonus[/green]\n")

        console.print("  [dim]# Show details for episode 390[/dim]")
        console.print("  [green]tgl.py info E390[/green]\n")

        console.print("  [dim]# Show details for bonus episode 5[/dim]")
        console.print("  [green]tgl.py info B05[/green]\n")

        console.print("  [dim]# Search for episodes about house music[/dim]")
        console.print("  [green]tgl.py search \"house music\"[/green]\n")

        console.print("  [dim]# Search for episodes with tracks by LAU[/dim]")
        console.print("  [green]tgl.py search LAU[/green]\n")

        console.print("  [dim]# Refresh the episode cache[/dim]")
        console.print("  [green]tgl.py refresh[/green]\n")

        console.print("[dim]For detailed help on any command, use:[/dim]")
        console.print("  [green]tgl.py [command] --help[/green]\n")


# ============================================================================
# Configuration
# ============================================================================

class Settings(BaseSettings):
    """Application settings loaded from environment variables

    Accepts both TGL_ prefixed and non-prefixed variable names for backward compatibility.
    Prefixed versions take priority if both exist.
    """
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )

    # Patreon RSS feed URL
    # Accepts: TGL_PATREON_RSS_URL or PATREON_RSS_URL
    patreon_rss_url: str = Field(
        ...,
        validation_alias=AliasChoices('TGL_PATREON_RSS_URL', 'PATREON_RSS_URL'),
        description="Patreon RSS feed URL with auth token"
    )

    # Spotify API credentials
    # Accepts: TGL_SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_ID
    spotify_client_id: str = Field(
        ...,
        validation_alias=AliasChoices('TGL_SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_ID'),
        description="Spotify API client ID"
    )

    # Accepts: TGL_SPOTIFY_CLIENT_SECRET or SPOTIFY_CLIENT_SECRET
    spotify_client_secret: str = Field(
        ...,
        validation_alias=AliasChoices('TGL_SPOTIFY_CLIENT_SECRET', 'SPOTIFY_CLIENT_SECRET'),
        description="Spotify API client secret"
    )

    # Accepts: TGL_SPOTIFY_REDIRECT_URI or SPOTIFY_REDIRECT_URI
    spotify_redirect_uri: str = Field(
        default='http://127.0.0.1:8888/callback',
        validation_alias=AliasChoices('TGL_SPOTIFY_REDIRECT_URI', 'SPOTIFY_REDIRECT_URI'),
        description="Spotify OAuth redirect URI"
    )

    # Accepts: TGL_SPOTIFY_PLAYLIST_NAME or SPOTIFY_PLAYLIST_NAME
    spotify_playlist_name: str = Field(
        default='guestlistr',
        validation_alias=AliasChoices('TGL_SPOTIFY_PLAYLIST_NAME', 'SPOTIFY_PLAYLIST_NAME'),
        description="Default Spotify playlist name"
    )


# Global settings instance
settings = Settings()


# ============================================================================
# Pydantic Models
# ============================================================================

class TrackInfo(BaseModel):
    """Track information model"""
    artist: str
    title: str


class Episode(BaseModel):
    """Episode metadata model"""
    id: int
    episode_id: Optional[str] = None  # Formatted ID like "E101" or "X01"
    title: str
    full_title: str
    description: str  # Raw HTML description
    description_text: Optional[str] = None  # Clean text before tracklist
    tracklist: Optional[List[TrackInfo]] = None  # Structured tracklist
    published: str
    year: Optional[int]
    link: str
    audio_url: Optional[str] = None
    episode_type: str = 'TGL'  # 'TGL' or 'BONUS'

    def model_post_init(self, __context):
        """Set default episode_id if not provided (for backward compatibility)"""
        if self.episode_id is None:
            if self.episode_type == 'TGL':
                self.episode_id = f"E{self.id}"
            else:
                # BONUS episodes use id >= 10000, subtract offset to get B number
                b_number = self.id - 10000 if self.id >= 10000 else self.id
                self.episode_id = f"B{b_number:02d}"

    class Config:
        json_schema_extra = {
            "example": {
                "id": 390,
                "title": "Love Songs and Haunted Nights",
                "full_title": "TGL E390: Love Songs and Haunted Nights",
                "description": "Episode description...",
                "description_text": "This week's episode...",
                "tracklist": [{"artist": "Prospa", "title": "Love Songs"}],
                "published": "2024-10-15",
                "year": 2024,
                "link": "https://patreon.com/...",
                "audio_url": "https://..."
            }
        }


class Track(BaseModel):
    """Track model for Spotify operations"""
    artist: str
    track: str
    query: str


# ============================================================================
# Metadata Cache Manager
# ============================================================================

class MetadataCache:
    """Manages episode metadata cache in .cache/ folder with auto-refresh"""

    CACHE_MAX_AGE_HOURS = 1  # Auto-refresh if cache is older than this

    def __init__(self, cache_dir: Path = Path(".cache")):
        self.cache_dir = cache_dir
        self.metadata_file = cache_dir / "episodes.json"
        self.cache_dir.mkdir(exist_ok=True)
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


# ============================================================================
# Search Index Manager (using Whoosh)
# ============================================================================

class SearchIndex:
    """Manages a Whoosh-based search index for episodes"""

    def __init__(self, cache_dir: Path = Path(".cache")):
        self.cache_dir = cache_dir
        self.index_dir = cache_dir / "search_index"
        self.cache_dir.mkdir(exist_ok=True)
        self.index_dir.mkdir(exist_ok=True)

        # Define schema for episode search
        self.schema = Schema(
            episode_id=ID(stored=True),
            episode_id_str=STORED(),
            title=TEXT(stored=True, field_boost=3.0),
            description=TEXT(field_boost=1.0),
            artists=TEXT(field_boost=5.0),
            track_titles=TEXT(field_boost=2.0),
            episode_type=STORED()
        )

        # Create or open index
        if index.exists_in(str(self.index_dir)):
            self.ix = index.open_dir(str(self.index_dir))
        else:
            self.ix = index.create_in(str(self.index_dir), self.schema)

    def build_index(self, episodes: Dict[int, Episode]):
        """Build search index from episodes using Whoosh"""
        # Clear existing index
        writer = AsyncWriter(self.ix)

        try:
            for ep_id, episode in episodes.items():
                # Collect all track artists and titles
                artists = []
                track_titles = []
                if episode.tracklist:
                    for track in episode.tracklist:
                        artists.append(track.artist)
                        track_titles.append(track.title)

                # Add document to index
                writer.add_document(
                    episode_id=str(ep_id),
                    episode_id_str=episode.episode_id or f"E{ep_id}",
                    title=episode.title,
                    description=episode.description_text or "",
                    artists=" ".join(artists),
                    track_titles=" ".join(track_titles),
                    episode_type=episode.episode_type
                )

            writer.commit()
        except Exception as e:
            writer.cancel()
            raise e

    def search(self, query: str, episodes: Dict[int, Episode]) -> List[Dict]:
        """Search episodes using Whoosh

        Returns list of matches with relevance scores.
        """
        # Create multifield parser
        parser = MultifieldParser(
            ["title", "description", "artists", "track_titles"],
            schema=self.schema,
            group=OrGroup
        )

        # Parse query
        q = parser.parse(query)

        results = []
        with self.ix.searcher() as searcher:
            search_results = searcher.search(q, limit=100, terms=True)

            for hit in search_results:
                ep_id = int(hit['episode_id'])
                episode = episodes.get(ep_id)

                if not episode:
                    continue

                # Determine match context based on which fields matched
                match_context = "Match found"
                matched_terms = hit.matched_terms()

                # Check which field had the match
                field_names = set(field for field, term in matched_terms)

                if 'artists' in field_names:
                    # Find which artist matched
                    if episode.tracklist:
                        query_lower = query.lower()
                        for track in episode.tracklist:
                            if query_lower in track.artist.lower():
                                match_context = f"Track Artist: {track.artist}"
                                break
                elif 'track_titles' in field_names:
                    # Find which track matched
                    if episode.tracklist:
                        query_lower = query.lower()
                        for track in episode.tracklist:
                            if query_lower in track.title.lower():
                                match_context = f"Track: {track.artist} - {track.title}"
                                break
                elif 'title' in field_names:
                    match_context = f"Title: {episode.title}"
                elif 'description' in field_names:
                    match_context = "Description match"

                results.append({
                    'episode': episode,
                    'score': hit.score,
                    'context': match_context
                })

        return results


# ============================================================================
# Patreon Podcast Fetcher
# ============================================================================

class PatreonPodcastFetcher:
    """Fetches podcast episodes from Patreon RSS feed"""

    def __init__(self, rss_url: str):
        self.rss_url = rss_url
        self.parser = TracklistParser()

    def parse_episode_id(self, title: str) -> Optional[int]:
        """Parse episode ID from various title formats"""
        # Try different patterns in order of specificity
        patterns = [
            r'\bE\s*(\d+)\b',                          # TGL E390, TGL E 390
            r'TGL\s*-?\s*(\d+)\b',                     # TGL 382, TGL - 382, TGL-382
            r'(?:The\s+)?Guestlist\s*-?\s*Episode\s+(\d+)',  # The Guestlist - Episode 300, The Guestlist Episode 1
            r'(?:The\s+)?G-?list\s*-?\s*Episode?\s*(\d+)',   # G-list 95, Guestlist - Episode 299
        ]

        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return int(match.group(1))

        return None

    def _extract_description_text(self, html_description: str) -> str:
        """Extract clean text before the tracklist begins"""
        clean_text = self.parser._strip_html(html_description)

        # Find where the tracklist section begins
        tracklist_markers = [
            'tracklist',
            'track list',
            'tracks:',
        ]

        lines = clean_text.split('\n')
        description_lines = []
        found_tracklist = False

        for line in lines:
            stripped = line.strip()
            line_lower = stripped.lower()

            # Check if this line marks the start of the tracklist
            if any(marker in line_lower for marker in tracklist_markers):
                found_tracklist = True
                break

            # Check if this line looks like a track entry (starts with # followed by Artist - Track)
            # More strict: must start with # or digit
            if re.match(r'^[#\d\.\)]+\s+.+?\s*[-–—]\s*.+', stripped):
                found_tracklist = True
                break

            # Don't break on regular sentences that happen to have dashes
            if stripped:
                description_lines.append(stripped)

        return '\n'.join(description_lines).strip()

    def _parse_structured_tracklist(self, html_description: str) -> List[TrackInfo]:
        """Parse tracklist into structured format"""
        clean_text = self.parser._strip_html(html_description)
        lines = clean_text.split('\n')
        tracks = []
        seen = set()
        in_tracklist = False

        # First pass: look for explicit tracklist markers or detect implicit tracklist
        # by finding multiple consecutive lines with "Artist - Track" format
        for i, line in enumerate(lines):
            line = line.strip()
            line_lower = line.lower()

            if any(marker in line_lower for marker in ['tracklist', 'track list', 'tracks:']):
                in_tracklist = True
                break

        # Second pass: try to detect implicit tracklist (multiple Artist - Track lines in a row)
        if not in_tracklist:
            consecutive_track_lines = 0
            prose_indicators = [
                r'\b(if|and|the|this|that|with|for|from|you|your|we|our|my|me|be|have|has|had|will|would|could|should|can|may|might|must|shall|do|does|did|is|am|are|was|were|been|being|happy|thanks|today|week|year|episode|podcast)\b',
                r"(?:n't|'ll|'ve|'re|'s)\b"
            ]

            for i, line in enumerate(lines):
                line = line.strip()
                if len(line) < 5 or len(line) > 100:
                    consecutive_track_lines = 0
                    continue

                # Check if line looks like "Artist - Track"
                cleaned = re.sub(r'^(?:RECORD OF THE WEEK|ROTW|FROM THE CRATES|ALSO RECOMMENDED):\s*', '', line, flags=re.IGNORECASE).strip()
                cleaned = re.sub(r'^[#\d\.\)]+\s*', '', cleaned).strip()

                match = re.match(r'^(.+?)\s*[-–—]\s*(.+)', cleaned)
                if match and not any(skip in line.lower() for skip in ['http', 'www.', 'patreon']):
                    # Check if artist part looks like prose
                    artist_part = match.group(1).lower()
                    is_prose = any(re.search(pattern, artist_part) for pattern in prose_indicators)

                    if not is_prose:
                        consecutive_track_lines += 1
                        if consecutive_track_lines >= 3:
                            # Found at least 3 consecutive track-like lines, assume it's a tracklist
                            in_tracklist = True
                            break
                    else:
                        consecutive_track_lines = 0
                else:
                    consecutive_track_lines = 0

        # Third pass: parse tracks
        for line in lines:
            line = line.strip()

            if not line or len(line) < 5:
                continue

            line_lower = line.lower()

            # Check if we've entered the tracklist section
            if any(marker in line_lower for marker in ['tracklist', 'track list', 'tracks:']):
                in_tracklist = True
                continue

            # Check if we've left the tracklist section (common section separators)
            if in_tracklist and any(marker in line_lower for marker in ['----', '====', 'best of', 'longlist', 'links:', 'support', 'patreon', 'thanks to', 'our love to']):
                in_tracklist = False
                continue

            # Handle special prefixes
            if line_lower.startswith('record of the week:') or line_lower.startswith('from the crates:') or line_lower.startswith('also recommended:'):
                # Extract the track from after the prefix
                prefix_match = re.match(r'^(?:RECORD OF THE WEEK|ROTW|FROM THE CRATES|ALSO RECOMMENDED):\s*(.+)', line, flags=re.IGNORECASE)
                if prefix_match:
                    line = prefix_match.group(1)

            # Check if line has a track marker
            has_track_marker = re.match(r'^[#\d\.\)]+\s', line)

            # Parse if: has marker OR in_tracklist section OR looks like track format
            cleaned_line = re.sub(r'^[#\d\.\)]+\s*', '', line).strip()
            track_pattern_match = re.match(r'^(.+?)\s*[-–—]\s*(.+)', cleaned_line)

            if not has_track_marker and not in_tracklist and not track_pattern_match:
                continue

            # Try to parse "Artist - Track" format
            match = re.match(r'^(.+?)\s*[-–—]\s*(.+?)(?:\s*\[[^\]]*\])?\s*(?:\s*\([^\)]*\))?\s*$', cleaned_line)

            if match:
                artist = match.group(1).strip()
                track_title = match.group(2).strip()

                # Clean up common prefixes from artist name
                artist = re.sub(r'^(?:RECORD OF THE WEEK|ROTW|FROM THE CRATES):\s*', '', artist, flags=re.IGNORECASE).strip()

                # Clean up track name
                track_title = re.sub(r'\s*\(Original Mix\)\s*$', '', track_title, flags=re.IGNORECASE)

                # Skip if too short or too long (likely not a track)
                if len(artist) < 2 or len(track_title) < 2:
                    continue

                if len(artist) > 100 or len(track_title) > 150:
                    continue

                # Skip URLs
                if 'http' in line.lower() or 'www.' in line.lower():
                    continue

                # Skip prose (unless we have an explicit marker like # or number)
                if not has_track_marker and not any(marker in clean_text.lower()[:200] for marker in ['tracklist', 'track list']):
                    prose_indicators = [
                        r'\b(if|and|the|this|that|with|for|from|you|your|we|our|my|me|be|have|has|had|will|would|could|should|can|may|might|must|shall|do|does|did|is|am|are|was|were|been|being|happy|thanks|today|week|year|episode|podcast)\b',
                        r"(?:n't|'ll|'ve|'re|'s)\b"
                    ]
                    is_prose = any(re.search(pattern, artist.lower()) for pattern in prose_indicators)
                    if is_prose:
                        continue

                # Avoid duplicates
                track_key = f"{artist.lower()}|{track_title.lower()}"
                if track_key not in seen:
                    seen.add(track_key)
                    tracks.append(TrackInfo(
                        artist=artist,
                        title=track_title
                    ))

        return tracks

    def classify_episode_type(self, title: str) -> str:
        """Classify episode as TGL or BONUS based on title"""
        title_lower = title.lower()

        # TGL episode patterns
        tgl_patterns = [
            r'\btgl\b',
            r'\bguestlist\b',
            r'\bepisode\s+\d+',
            r'\be[\s:]*\d+',
        ]

        for pattern in tgl_patterns:
            if re.search(pattern, title_lower):
                return 'TGL'

        # Everything else is BONUS (From The Crates, Fear of Tigers, re-ups, etc.)
        return 'BONUS'

    def assign_episode_id(self, title: str, episode_type: str, bonus_counter: int) -> str:
        """Assign episode ID based on type

        Args:
            title: Episode title
            episode_type: 'TGL' or 'BONUS'
            bonus_counter: Counter for BONUS episodes

        Returns:
            Episode ID (E{num} for TGL, B{num} for BONUS)
        """
        if episode_type == 'TGL':
            # Try to parse numeric ID from title
            numeric_id = self.parse_episode_id(title)
            if numeric_id:
                return f"E{numeric_id}"
            else:
                # Couldn't parse ID, use E???
                return "E???"
        else:
            # BONUS episodes get B prefix
            return f"B{bonus_counter:02d}"

    def fetch_episodes(self, limit: Optional[int] = None) -> List[Episode]:
        """Fetch episodes from the RSS feed"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; TGL-CLI/1.0)'
            }
            response = requests.get(self.rss_url, headers=headers, timeout=30)
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if feed.bozo:
                console.print(f"[yellow]Warning: Feed parsing encountered an issue: {feed.bozo_exception}[/yellow]")

            # First pass: collect all episodes
            temp_episodes = []
            entries_to_process = feed.entries if limit is None else feed.entries[:limit]

            for entry in entries_to_process:
                title = entry.get('title', '')

                # Classify episode type
                episode_type = self.classify_episode_type(title)

                # Extract clean title (after colon)
                clean_title = title
                if ':' in title:
                    clean_title = title.split(':', 1)[1].strip()

                # Parse published date
                published_parsed = entry.get('published_parsed')
                year = None
                published = entry.get('published', '')
                if published_parsed:
                    year = published_parsed.tm_year
                    published = time.strftime('%Y-%m-%d', published_parsed)

                # Get audio URL
                audio_url = None
                if hasattr(entry, 'enclosures') and entry.enclosures:
                    audio_url = entry.enclosures[0].get('href')

                # Get full description
                raw_description = entry.get('description', '') or entry.get('summary', '')

                # Parse description text and tracklist
                description_text = self._extract_description_text(raw_description)
                tracklist = self._parse_structured_tracklist(raw_description)

                # Store episode data temporarily
                temp_episodes.append({
                    'title': title,
                    'clean_title': clean_title,
                    'episode_type': episode_type,
                    'published': published,
                    'published_parsed': published_parsed,
                    'year': year,
                    'link': entry.get('link', ''),
                    'audio_url': audio_url,
                    'description': raw_description,
                    'description_text': description_text,
                    'tracklist': tracklist
                })

            # Second pass: assign IDs to BONUS episodes sequentially
            # Sort BONUS episodes by published date (oldest first)
            bonus_episodes = [ep for ep in temp_episodes if ep['episode_type'] == 'BONUS']
            bonus_episodes.sort(key=lambda ep: ep['published_parsed'] if ep['published_parsed'] else time.struct_time((1970, 1, 1, 0, 0, 0, 0, 1, 0)))

            # Create mapping from link to B number
            bonus_id_map = {}
            for idx, ep in enumerate(bonus_episodes, start=1):
                bonus_id_map[ep['link']] = idx

            # Third pass: create Episode objects with proper IDs
            episodes = []
            for ep_data in temp_episodes:
                episode_type = ep_data['episode_type']

                if episode_type == 'TGL':
                    numeric_id = self.parse_episode_id(ep_data['title'])
                    if numeric_id is None:
                        numeric_id = 0  # Fallback for unparseable TGL episodes
                    episode_id_str = f"E{numeric_id}" if numeric_id > 0 else "E???"
                else:
                    # BONUS episodes use sequential numbering with offset to avoid conflicts
                    # Use 10000 + sequence number as the internal numeric ID
                    b_number = bonus_id_map[ep_data['link']]
                    numeric_id = 10000 + b_number
                    episode_id_str = f"B{b_number:02d}"

                # Build normalized full_title with formatted ID
                if episode_type == 'TGL':
                    full_title = f"TGL {episode_id_str}: {ep_data['clean_title']}"
                else:
                    full_title = f"BONUS {episode_id_str}: {ep_data['clean_title']}"

                episode = Episode(
                    id=numeric_id,
                    episode_id=episode_id_str,
                    title=ep_data['clean_title'],
                    full_title=full_title,
                    description=ep_data['description'],
                    description_text=ep_data['description_text'],
                    tracklist=ep_data['tracklist'] if ep_data['tracklist'] else None,
                    published=ep_data['published'],
                    year=ep_data['year'],
                    link=ep_data['link'],
                    audio_url=ep_data['audio_url'],
                    episode_type=episode_type
                )
                episodes.append(episode)

            return episodes

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error fetching RSS feed: {e}[/red]")
            return []


# ============================================================================
# Tracklist Parser
# ============================================================================

class TracklistParser:
    """Parses tracklists from episode show notes"""

    def _strip_html(self, html_text: str) -> str:
        """Strip HTML tags and unescape HTML entities"""
        text = re.sub(r'<[^>]+>', '\n', html_text)
        text = unescape(text)
        return text

    def parse_tracklist(self, description: str) -> List[Track]:
        """Extract tracks from episode description"""
        clean_text = self._strip_html(description)

        tracks = []
        seen = set()

        for line in clean_text.split('\n'):
            line = line.strip()

            if not line or len(line) < 5:
                continue

            if any(marker in line.lower() for marker in ['tracklist', 'record of the week', 'from the crates', 'also recommended', 'guestmix']):
                continue

            line = re.sub(r'^[#\d\.\)]+\s*', '', line).strip()

            match = re.match(r'^(.+?)\s*[-–—]\s*(.+?)(?:\s*\([^\)]*\))?\s*$', line)

            if match:
                artist = match.group(1).strip()
                track_name = match.group(2).strip()

                track_name = re.sub(r'\s*\(Original Mix\)\s*$', '', track_name, flags=re.IGNORECASE)

                if len(artist) < 2 or len(track_name) < 2:
                    continue

                if 'http' in line.lower() or 'www.' in line.lower():
                    continue

                track_key = f"{artist.lower()}|{track_name.lower()}"
                if track_key not in seen:
                    seen.add(track_key)
                    tracks.append(Track(
                        artist=artist,
                        track=track_name,
                        query=f"{artist} {track_name}"
                    ))

        return tracks


# ============================================================================
# Spotify Playlist Manager
# ============================================================================

class SpotifyPlaylistManager:
    """Manages Spotify playlist creation and track additions"""

    def __init__(self):
        scope = "playlist-modify-public playlist-modify-private"
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            redirect_uri=settings.spotify_redirect_uri,
            scope=scope
        ))
        self.user_id = self.sp.current_user()['id']

    def search_track(self, track: Track) -> Optional[str]:
        """Search for a track on Spotify and return the URI"""
        try:
            results = self.sp.search(q=track.query, type='track', limit=1)
            if results['tracks']['items']:
                return results['tracks']['items'][0]['uri']
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
        """Add tracks to a playlist (in batches of 100)"""
        seen = set()
        unique_uris = []
        for uri in track_uris:
            if uri not in seen:
                seen.add(uri)
                unique_uris.append(uri)

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


# ============================================================================
# State Manager (for Spotify integration)
# ============================================================================

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


# ============================================================================
# CLI Commands
# ============================================================================

@app.command()
def refresh():
    """Refresh episode metadata cache from RSS feed"""
    console.print("\n[bold cyan]" + "═" * 60)
    console.print("[bold cyan]Refreshing Episode Metadata")
    console.print("[bold cyan]" + "═" * 60 + "\n")

    cache = MetadataCache()
    fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Fetching episodes from RSS feed...", total=None)
        episodes = fetcher.fetch_episodes()
        progress.update(task, completed=True, total=1)

    console.print(f"[green]✓[/green] Fetched {len(episodes)} episodes\n")

    # Update cache
    for episode in episodes:
        cache.add_episode(episode)

    cache.save()

    # Build search index
    console.print("[cyan]Building search index...[/cyan]")
    search_index = SearchIndex(cache.cache_dir)
    search_index.build_index(cache.episodes)
    console.print(f"[green]✓[/green] Search index built\n")

    console.print(f"[bold green]✓ Done![/bold green] Cached {len(episodes)} episodes")
    console.print("[bold cyan]" + "═" * 60 + "\n")


@app.command()
def list(
    year: Optional[int] = typer.Option(None, "--year", help="Filter by year"),
    tgl: bool = typer.Option(False, "--tgl", help="Show only TGL episodes"),
    bonus: bool = typer.Option(False, "--bonus", help="Show only BONUS episodes")
):
    """List all episodes"""
    cache = MetadataCache()

    # Auto-refresh if cache is stale or empty
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    episodes = cache.get_episodes_by_year(year) if year else cache.get_all_episodes()

    # Apply type filters
    if tgl and not bonus:
        episodes = [ep for ep in episodes if ep.episode_type == 'TGL']
    elif bonus and not tgl:
        episodes = [ep for ep in episodes if ep.episode_type == 'BONUS']
    # If both or neither, show all

    if not episodes:
        console.print(f"[yellow]No episodes found[/yellow]")
        raise typer.Exit(1)

    # Show overview
    console.print()
    if year:
        # When filtering by year, just show total
        tgl_count = sum(1 for ep in episodes if ep.episode_type == 'TGL')
        bonus_count = sum(1 for ep in episodes if ep.episode_type == 'BONUS')
        console.print(f"[bold cyan]{year}:[/bold cyan] {len(episodes)} episodes total ([cyan]🎧 {tgl_count}[/cyan], [magenta]🎁 {bonus_count}[/magenta])")
    else:
        # Show breakdown by year
        from collections import defaultdict
        year_stats = defaultdict(lambda: {'TGL': 0, 'BONUS': 0})

        for episode in episodes:
            year_stats[episode.year][episode.episode_type] += 1

        overview_table = Table(show_header=True, header_style="bold cyan", box=None)
        overview_table.add_column("Year", style="cyan", justify="center")
        overview_table.add_column("🎧 TGL", style="cyan", justify="right")
        overview_table.add_column("🎁 BONUS", style="magenta", justify="right")
        overview_table.add_column("Total", style="green", justify="right")

        total_tgl = 0
        total_bonus = 0

        for yr in sorted(year_stats.keys(), reverse=True):
            tgl = year_stats[yr]['TGL']
            bonus = year_stats[yr]['BONUS']
            total = tgl + bonus
            total_tgl += tgl
            total_bonus += bonus
            overview_table.add_row(str(yr), str(tgl), str(bonus), str(total))

        # Add totals row
        overview_table.add_row(
            "[bold]Total[/bold]",
            f"[bold]{total_tgl}[/bold]",
            f"[bold]{total_bonus}[/bold]",
            f"[bold]{total_tgl + total_bonus}[/bold]"
        )

        console.print(overview_table)
    console.print()

    table = Table(title=f"TGL Episodes{f' ({year})' if year else ''}", show_header=True, header_style="bold cyan")
    table.add_column("Type", justify="center", width=4)
    table.add_column("ID", style="green", justify="right", width=6)
    table.add_column("Title", style="white", no_wrap=False, overflow="fold")
    table.add_column("Tracks", style="dim", justify="center", width=6)
    table.add_column("Date", style="yellow", width=12)

    for episode in episodes:
        # Create clickable episode ID
        clickable_id = Text(episode.episode_id)
        clickable_id.stylize(f"link {episode.link}")

        # Get episode type icon
        type_icon = "🎧" if episode.episode_type == "TGL" else "🎁"

        # Get track count
        track_count = str(len(episode.tracklist)) if episode.tracklist else "-"

        table.add_row(type_icon, clickable_id, episode.title, track_count, episode.published)

    console.print(table)
    console.print(f"\n[dim]Total: {len(episodes)} episodes[/dim]")
    console.print(f"[dim]💡 Tip: Click on episode IDs to open in browser[/dim]\n")


def parse_episode_id(episode_id_str: str) -> int:
    """Parse episode ID string to internal numeric ID

    Accepts:
    - Plain numbers: "390" -> 390 (TGL episode)
    - E prefix: "E390" -> 390 (TGL episode)
    - B prefix: "B05" -> 10005 (BONUS episode, 10000 + 5)
    """
    episode_id_str = episode_id_str.strip().upper()

    if episode_id_str.startswith('E'):
        # TGL episode
        try:
            return int(episode_id_str[1:])
        except ValueError:
            raise ValueError(f"Invalid episode ID format: {episode_id_str}")
    elif episode_id_str.startswith('B'):
        # BONUS episode
        try:
            b_number = int(episode_id_str[1:])
            return 10000 + b_number
        except ValueError:
            raise ValueError(f"Invalid episode ID format: {episode_id_str}")
    else:
        # Plain number, assume TGL episode
        try:
            return int(episode_id_str)
        except ValueError:
            raise ValueError(f"Invalid episode ID format: {episode_id_str}")


@app.command(name="info")
@app.command(name="show")
def info(episode_id: str):
    """Show details for a specific episode"""
    cache = MetadataCache()

    # Auto-refresh if cache is stale or empty
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    # Parse episode ID
    try:
        numeric_id = parse_episode_id(episode_id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    episode = cache.get_episode(numeric_id)

    if not episode:
        console.print(f"[red]Episode {episode_id} not found[/red]")
        raise typer.Exit(1)

    # Display episode info with clickable ID
    clickable_id = f"[link={episode.link}]{episode.episode_id}[/link]"
    console.print(f"\n[bold cyan]{clickable_id}:[/bold cyan] [bold white]{episode.title}[/bold white]")
    console.print(f"[dim]Published: {episode.published}[/dim]")

    # Display description text
    if episode.description_text:
        console.print(f"\n[bold]Description:[/bold]")
        # Limit to first 500 chars to keep it concise
        desc = episode.description_text
        if len(desc) > 500:
            desc = desc[:500] + "..."
        console.print(f"[dim]{desc}[/dim]")

    # Display structured tracklist
    if episode.tracklist:
        console.print(f"\n[bold]Tracklist ({len(episode.tracklist)} tracks):[/bold]")
        for i, track in enumerate(episode.tracklist, 1):
            console.print(f"  {i:3d}. {track.artist} - {track.title}")
    else:
        console.print("\n[yellow]No tracklist found[/yellow]")

    console.print()


@app.command()
def search(
    query: List[str] = typer.Argument(..., help="Search query (multiple words allowed)")
):
    """Search episodes by title, description, or tracks

    You can search with multiple words without quotes:
    tgl.py search Fabrizio Mammarella
    """
    if not query:
        console.print("[red]Error: Please provide a search query[/red]")
        raise typer.Exit(1)

    # Join query words into a single string
    search_query = ' '.join(query)

    cache = MetadataCache()

    # Auto-refresh if cache is stale or empty
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    # Load search index
    search_index = SearchIndex(cache.cache_dir)

    # Check if index is empty and rebuild if needed
    with search_index.ix.searcher() as searcher:
        if searcher.doc_count_all() == 0:
            console.print("[cyan]Building search index...[/cyan]")
            search_index.build_index(cache.episodes)
            console.print("[green]✓[/green] Search index built")

    # Perform search
    results = search_index.search(search_query, cache.episodes)

    if not results:
        console.print(f"[yellow]No episodes found matching '{search_query}'[/yellow]")
        return

    console.print(f"\n[bold cyan]Search Results for '{search_query}'[/bold cyan]")
    console.print(f"[dim]Found {len(results)} matches[/dim]\n")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Relevance", justify="right", width=8)
    table.add_column("Type", justify="center", width=4)
    table.add_column("ID", style="green", justify="right", width=6)
    table.add_column("Title", style="white")
    table.add_column("Match Context", style="dim")

    for result in results[:20]:  # Limit to top 20 results
        episode = result['episode']
        score = result['score']
        context = result['context']

        # Create clickable episode ID
        clickable_id = Text(episode.episode_id)
        clickable_id.stylize(f"link {episode.link}")

        # Get episode type icon
        type_icon = "🎧" if episode.episode_type == "TGL" else "🎁"

        # Format score as percentage (cap at 100%)
        relevance = f"{min(score * 100, 100):.0f}%"

        table.add_row(relevance, type_icon, clickable_id, episode.title, context[:60])

    console.print(table)
    console.print(f"\n[dim]Showing top {min(len(results), 20)} results[/dim]\n")


@app.command()
def download(episode_id: str):
    """Download an episode audio file"""
    cache = MetadataCache()

    # Auto-refresh if cache is stale or empty
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    # Parse episode ID
    try:
        numeric_id = parse_episode_id(episode_id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    episode = cache.get_episode(numeric_id)

    if not episode:
        console.print(f"[red]Episode {episode_id} not found[/red]")
        raise typer.Exit(1)

    if not episode.audio_url:
        console.print(f"[red]No audio URL found for episode {episode_id}[/red]")
        raise typer.Exit(1)

    # Create episodes directory
    episodes_dir = Path("episodes")
    episodes_dir.mkdir(exist_ok=True)

    # Create filename
    filename = f"TGL #{episode.id} - {episode.title}.mp3"
    # Clean filename
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filepath = episodes_dir / filename

    if filepath.exists():
        console.print(f"[yellow]File already exists: {filepath}[/yellow]")
        if not typer.confirm("Overwrite?"):
            raise typer.Exit(0)

    console.print(f"\n[cyan]Downloading: {episode.full_title}[/cyan]")
    console.print(f"[dim]Saving to: {filepath}[/dim]\n")

    try:
        response = requests.get(episode.audio_url, stream=True, timeout=30)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Downloading...", total=total_size)

            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

        console.print(f"\n[bold green]✓ Downloaded successfully![/bold green]")
        console.print(f"[dim]Saved to: {filepath}[/dim]\n")

    except requests.exceptions.RequestException as e:
        console.print(f"\n[red]Error downloading episode: {e}[/red]")
        if filepath.exists():
            filepath.unlink()
        raise typer.Exit(1)


@app.command()
def spotify(
    episodes_limit: Optional[int] = typer.Option(None, "-n", "--episodes", help="Number of recent episodes to process"),
    year: Optional[int] = typer.Option(None, "--year", help="Filter episodes by year"),
    dryrun: bool = typer.Option(False, "--dryrun", help="Dry run mode (no Spotify operations)"),
    force_refresh: bool = typer.Option(False, "--force-refresh", help="Bypass cache, reprocess all episodes"),
    use_cache: bool = typer.Option(False, "--use-cache", help="Use cache even when filtering by year"),
):
    """Import episode tracklists to Spotify playlist"""
    console.print("\n[bold cyan]" + "═" * 60)
    console.print("[bold cyan]TGL to Spotify Playlist")
    if dryrun:
        console.print("[bold yellow]🔍 DRY RUN MODE[/bold yellow]")
    console.print("[bold cyan]" + "═" * 60 + "\n")

    # Add year to playlist name if filtering
    PLAYLIST_NAME = f"{settings.spotify_playlist_name} {year}" if year else settings.spotify_playlist_name

    # Load cached metadata with auto-refresh
    cache = MetadataCache()

    # Auto-refresh if cache is stale or empty
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    # Use separate state file for dryrun mode
    state_file = ".guestlistr_state_dryrun.json" if dryrun else ".guestlistr_state.json"
    state = StateManager(state_file)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:

        # Get episodes from cache
        fetch_task = progress.add_task("[cyan]Loading episodes from cache...", total=None)

        if year:
            fetched_episodes = cache.get_episodes_by_year(year)
            if episodes_limit:
                fetched_episodes = fetched_episodes[:episodes_limit]
        else:
            # Get all episodes sorted by ID descending (newest first)
            fetched_episodes = sorted(cache.episodes.values(), key=lambda e: e.id, reverse=True)
            if episodes_limit:
                fetched_episodes = fetched_episodes[:episodes_limit]

        progress.update(fetch_task, completed=True, total=1)

        if not fetched_episodes:
            console.print("[red]No episodes found![/red]")
            raise typer.Exit(1)

        console.print(f"[green]✓[/green] Loaded {len(fetched_episodes)} episodes\n")

        # Filter out already-processed episodes
        should_use_cache = not force_refresh and (not year or use_cache)

        if should_use_cache:
            new_episodes = [ep for ep in fetched_episodes if not state.is_episode_processed(ep.link)]
            cached_count = len(fetched_episodes) - len(new_episodes)

            if cached_count > 0:
                console.print(f"[cyan]ℹ[/cyan] Skipping {cached_count} already-processed episodes\n")

            fetched_episodes = new_episodes

        if not fetched_episodes:
            console.print("[yellow]No new episodes to process[/yellow]\n")
            return

        console.print(f"[cyan]Processing {len(fetched_episodes)} episodes...[/cyan]\n")

        # Get tracklists from cached episodes
        all_tracks = []
        episode_tracks = {}

        parse_task = progress.add_task("[cyan]Loading tracklists...", total=len(fetched_episodes))

        for episode in fetched_episodes:
            # Use cached tracklist
            tracks = episode.tracklist if episode.tracklist else []
            # Convert Track objects to dict format expected by the rest of the code
            track_dicts = [Track(artist=t.artist, track=t.title, query=f"{t.artist} {t.title}") for t in tracks]
            all_tracks.extend(track_dicts)
            episode_tracks[episode.link] = track_dicts
            progress.update(parse_task, advance=1)

        console.print(f"[green]✓[/green] Loaded {len(all_tracks)} tracks from {len(fetched_episodes)} episodes\n")

        if dryrun:
            # Dry run mode
            console.print("[yellow]⚠ Dry run mode - skipping Spotify operations[/yellow]\n")
            console.print(f"[cyan]Summary:[/cyan]")
            console.print(f"  • Episodes processed: {len(fetched_episodes)}")
            console.print(f"  • Total tracks extracted: {len(all_tracks)}")
            console.print(f"\n[dim]Tracks found:[/dim]")
            for i, track in enumerate(all_tracks[:10], 1):
                console.print(f"  {i}. {track.artist} - {track.track}")
            if len(all_tracks) > 10:
                console.print(f"  ... and {len(all_tracks) - 10} more")
        else:
            # Initialize Spotify
            console.print("[cyan]Initializing Spotify connection...[/cyan]")
            spotify_manager = SpotifyPlaylistManager()
            console.print("[green]✓[/green] Connected to Spotify\n")

            # Get retryable failed tracks
            retryable_tracks = state.get_retryable_failed_tracks()

            if retryable_tracks:
                console.print(f"[cyan]ℹ[/cyan] Found {len(retryable_tracks)} previously failed tracks to retry\n")

            total_tracks_to_search = len(all_tracks) + len(retryable_tracks)
            track_uris = []

            if total_tracks_to_search > 0:
                search_task = progress.add_task("[cyan]Searching tracks on Spotify...", total=total_tracks_to_search)

                # Search new tracks
                for episode in fetched_episodes:
                    ep_tracks = episode_tracks.get(episode.link, [])

                    for track in ep_tracks:
                        uri = spotify_manager.search_track(track)
                        if uri:
                            track_uris.append(uri)
                        else:
                            state.add_failed_track(track, episode.full_title)
                        progress.update(search_task, advance=1)

                    # Mark episode as processed and save
                    state.mark_episode_processed(episode, len(ep_tracks))
                    state.save()

                # Retry failed tracks
                retry_found = 0
                for track_info in retryable_tracks:
                    track = Track(artist=track_info['artist'], track=track_info['track'], query=track_info['query'])
                    uri = spotify_manager.search_track(track)
                    if uri:
                        track_uris.append(uri)
                        state.remove_failed_track(track_info['key'])
                        retry_found += 1
                    else:
                        state.add_failed_track(track, "retry")
                    progress.update(search_task, advance=1)

                if retryable_tracks:
                    state.save()

                if retry_found > 0:
                    console.print(f"[green]✓[/green] Found {retry_found} previously failed tracks!\n")

                console.print(f"[green]✓[/green] Found {len(track_uris)}/{total_tracks_to_search} tracks on Spotify\n")

            # Create or update playlist
            playlist_id = spotify_manager.get_playlist_by_name(PLAYLIST_NAME)

            if playlist_id:
                console.print(f"[cyan]Found existing playlist:[/cyan] {PLAYLIST_NAME}")
                existing_tracks = spotify_manager.get_playlist_tracks(playlist_id)
                new_tracks = [uri for uri in track_uris if uri not in existing_tracks]

                if new_tracks:
                    console.print(f"[cyan]Adding {len(new_tracks)} new tracks...[/cyan]")
                    num_added = spotify_manager.add_tracks_to_playlist(playlist_id, new_tracks)
                    console.print(f"[green]✓[/green] Added {num_added} tracks to playlist")
                else:
                    console.print("[yellow]No new tracks to add[/yellow]")
            else:
                console.print(f"[cyan]Creating new playlist:[/cyan] {PLAYLIST_NAME}")
                playlist_id = spotify_manager.create_playlist(
                    name=PLAYLIST_NAME,
                    description="Tracks from TGL (The Guestlist) podcast"
                )
                num_added = spotify_manager.add_tracks_to_playlist(playlist_id, track_uris)
                console.print(f"[green]✓[/green] Created playlist and added {num_added} tracks")

            playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
            console.print(f"\n[bold green]✓ Done![/bold green] Playlist URL: [link={playlist_url}]{playlist_url}[/link]")

    console.print("[bold cyan]" + "═" * 60 + "\n")


if __name__ == "__main__":
    app()
