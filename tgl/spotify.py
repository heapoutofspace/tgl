"""Spotify integration for TGL

This module handles all Spotify operations including:
- Track searching with caching
- Playlist creation and synchronization
- Two auth flows: client credentials (search) and authorization code (playlists)
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth
from spotipy.cache_handler import CacheHandler
from rich.console import Console
from pydantic import BaseModel, Field

from .config import Settings, paths
from .models import Episode, TrackInfo

console = Console()


class SpotifyTrackCache(BaseModel):
    """Cached Spotify track search result"""
    id: Optional[str] = None
    name: Optional[str] = None
    artists: Optional[List[str]] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class SpotifyPlaylist(BaseModel):
    """Spotify playlist state"""
    id: str
    name: str
    tracks: List[str] = Field(default_factory=list)
    cover_version: Optional[str] = None


class SpotifyState(BaseModel):
    """Spotify manager state persisted to spotify.json"""
    tracks: Dict[str, SpotifyTrackCache] = Field(default_factory=dict)
    playlists: Dict[str, SpotifyPlaylist] = Field(default_factory=dict)
    oauth_token: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump(mode='json')

    @classmethod
    def from_dict(cls, data: Dict) -> 'SpotifyState':
        """Create from dictionary, handling legacy formats"""
        # Handle legacy track cache format (plain dicts)
        tracks = {}
        for key, value in data.get('tracks', {}).items():
            if isinstance(value, dict):
                tracks[key] = SpotifyTrackCache(**value)
            else:
                tracks[key] = value

        # Handle legacy playlist format (plain dicts)
        playlists = {}
        for key, value in data.get('playlists', {}).items():
            if isinstance(value, dict):
                playlists[key] = SpotifyPlaylist(**value)
            else:
                playlists[key] = value

        return cls(
            tracks=tracks,
            playlists=playlists,
            oauth_token=data.get('oauth_token')
        )


class IntegratedCacheHandler(CacheHandler):
    """Custom cache handler that stores OAuth tokens in spotify.json

    This integrates the OAuth token cache with our main Spotify state file,
    eliminating the need for a separate .spotify_cache file.

    Uses the SpotifyManager's in-memory state to avoid conflicts between
    the cache handler and the manager's state.
    """

    def __init__(self, state: SpotifyState, state_file: Path):
        """Initialize cache handler

        Args:
            state: Reference to SpotifyManager's SpotifyState instance
            state_file: Path to spotify.json file (for persistence)
        """
        self.state = state
        self.state_file = state_file

    def get_cached_token(self) -> Optional[Dict]:
        """Get cached OAuth token from in-memory state

        Returns:
            Token info dict or None if not found
        """
        return self.state.oauth_token

    def save_token_to_cache(self, token_info: Dict):
        """Save OAuth token to in-memory state and persist to file

        Args:
            token_info: Token info dict from Spotify OAuth
        """
        # Update in-memory state
        self.state.oauth_token = token_info

        # Persist to file immediately
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump(self.state.to_dict(), f, indent=2)
        except IOError as e:
            console.print(f"[red]Error saving OAuth token: {e}[/red]")


class SpotifyManager:
    """Manages Spotify operations with state persistence and smart caching"""

    def __init__(self, settings: Settings, dry_run: bool = False, verbose: bool = False, force_search_missing: bool = False):
        """Initialize Spotify manager

        Args:
            settings: Application settings with Spotify credentials
            dry_run: If True, no write operations are performed on Spotify
            verbose: If True, log all Spotify API calls
            force_search_missing: If True, ignore cache for missing tracks (re-search them)
        """
        self.settings = settings
        self.dry_run = dry_run
        self.verbose = verbose
        self.force_search_missing = force_search_missing
        self.state_file = paths.data_dir / "spotify.json"
        self.state = self._load_state()

        # Lazy-initialized clients
        self._search_client = None
        self._user_client = None
        self._user_id = None

    def _load_state(self) -> SpotifyState:
        """Load state from spotify.json"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                return SpotifyState.from_dict(data)
            except (json.JSONDecodeError, IOError) as e:
                console.print(f"[yellow]Warning: Could not load Spotify state: {e}[/yellow]")
                return self._empty_state()
        return self._empty_state()

    def _empty_state(self) -> SpotifyState:
        """Return empty state structure"""
        return SpotifyState()

    def _log_api_call(self, operation: str, details: str = ""):
        """Log Spotify API call in verbose mode

        Args:
            operation: Type of operation (e.g., "SEARCH", "CREATE_PLAYLIST", "ADD_TRACKS")
            details: Additional details about the operation
        """
        if self.verbose:
            if details:
                console.print(f"[dim]API: {operation} - {details}[/dim]")
            else:
                console.print(f"[dim]API: {operation}[/dim]")

    def _upload_cover(self, playlist_id: str, text: str | None = None):
        """Upload cover art to a playlist

        Args:
            playlist_id: Spotify playlist ID
            text: Text to render on cover (year or episode ID). If None, uses plain cover.
        """
        from .cover import generate_cover_art, COVER_VERSION

        if self.dry_run:
            cover_desc = text if text else "plain cover"
            console.print(f"[dim]Would upload cover: {cover_desc}[/dim]")
            return

        try:
            # Generate cover art as base64
            image_data = generate_cover_art(text)

            # Upload to Spotify
            client = self._get_user_client()
            cover_desc = text if text else "plain cover"
            self._log_api_call("UPLOAD_COVER", cover_desc)
            client.playlist_upload_cover_image(playlist_id, image_data)
            console.print(f"[green]✓[/green] Cover art uploaded")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not upload cover art: {e}[/yellow]")

    def _save_state(self, tracks_only: bool = False):
        """Save state to spotify.json

        Args:
            tracks_only: If True, only save track lookups (used in dry-run mode)
        """
        if self.dry_run and not tracks_only:
            return  # Don't save playlist state in dry run mode

        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump(self.state.to_dict(), f, indent=2)
        except IOError as e:
            console.print(f"[red]Error saving Spotify state: {e}[/red]")

    def _get_search_client(self) -> spotipy.Spotify:
        """Get or create client credentials client for track searching"""
        if self._search_client is None:
            auth_manager = SpotifyClientCredentials(
                client_id=self.settings.spotify_client_id,
                client_secret=self.settings.spotify_client_secret
            )
            self._search_client = spotipy.Spotify(auth_manager=auth_manager)
        return self._search_client

    def _get_user_client(self) -> spotipy.Spotify:
        """Get or create user auth client for playlist operations"""
        if self._user_client is None:
            scope = "playlist-modify-public playlist-modify-private playlist-read-private ugc-image-upload"
            # Use integrated cache handler that stores tokens in spotify.json
            # Pass in-memory state to ensure consistency
            cache_handler = IntegratedCacheHandler(self.state, self.state_file)
            auth_manager = SpotifyOAuth(
                client_id=self.settings.spotify_client_id,
                client_secret=self.settings.spotify_client_secret,
                redirect_uri=self.settings.spotify_redirect_uri,
                scope=scope,
                cache_handler=cache_handler,
                open_browser=True
            )
            self._user_client = spotipy.Spotify(auth_manager=auth_manager)
            # Get user ID
            self._user_id = self._user_client.current_user()['id']
        return self._user_client

    def authorize(self) -> bool:
        """Run authorization flow to ensure we have API access

        Returns:
            True if authorization successful, False otherwise
        """
        try:
            console.print("\n[cyan]Running Spotify authorization...[/cyan]")
            client = self._get_user_client()
            user_info = client.current_user()
            console.print(f"[green]✓[/green] Authorized as: {user_info['display_name']} ({user_info['id']})\n")
            return True
        except Exception as e:
            console.print(f"[red]✗ Authorization failed: {e}[/red]\n")
            return False

    def _make_search_key(self, artist: str, title: str, variant: Optional[str] = None) -> str:
        """Create a unique search key for caching

        Args:
            artist: Track artist
            title: Track title
            variant: Optional track variant (remix, feat, etc)

        Returns:
            Normalized search key
        """
        key = f"{artist.lower()}|{title.lower()}"
        if variant:
            key += f"|{variant.lower()}"
        return key

    def _normalize_for_comparison(self, text: str) -> str:
        """Normalize text for fuzzy matching

        Args:
            text: Text to normalize

        Returns:
            Normalized text
        """
        text = text.lower()
        # Replace separators
        text = text.replace('&', ',').replace(' and ', ',')
        # Normalize ellipsis (both ... and unicode …)
        text = text.replace('…', ' ').replace('...', ' ')
        # Remove extra whitespace
        text = ' '.join(text.split())
        # Remove punctuation that doesn't affect meaning
        for char in ['.', '!', '?']:
            text = text.replace(char, '')
        return text

    def _strings_similar(self, s1: str, s2: str, threshold: float = 0.85) -> bool:
        """Check if two strings are similar (for typo tolerance)

        Args:
            s1: First string
            s2: Second string
            threshold: Similarity threshold (0-1)

        Returns:
            True if strings are similar enough
        """
        # Normalize both strings
        s1 = s1.lower().replace(' ', '')
        s2 = s2.lower().replace(' ', '')

        # If identical, return True
        if s1 == s2:
            return True

        # Simple character-based similarity
        # Count matching characters in same positions
        if len(s1) == 0 or len(s2) == 0:
            return False

        # Use the longer string as base
        longer = s1 if len(s1) >= len(s2) else s2
        shorter = s2 if len(s1) >= len(s2) else s1

        # Count matching characters (position-independent)
        matches = 0
        shorter_chars = list(shorter)
        for char in longer:
            if char in shorter_chars:
                matches += 1
                shorter_chars.remove(char)

        similarity = matches / len(longer)
        return similarity >= threshold

    def _verify_track_match(self, track_data: Dict, expected_artist: str, expected_title: str) -> bool:
        """Verify that a Spotify track matches our search criteria

        Args:
            track_data: Track data from Spotify API
            expected_artist: Expected artist name
            expected_title: Expected track title

        Returns:
            True if track matches, False otherwise
        """
        # Get track name and artists from Spotify
        track_name = track_data['name'].lower()
        spotify_artists = [artist['name'].lower() for artist in track_data['artists']]

        # Normalize expected values
        expected_title_norm = self._normalize_for_comparison(expected_title)
        expected_artist_norm = self._normalize_for_comparison(expected_artist)
        track_name_norm = self._normalize_for_comparison(track_name)

        # Check if title matches (multiple strategies)
        title_match = (
            # Substring match
            expected_title_norm in track_name_norm or
            track_name_norm in expected_title_norm or
            # Match without spaces (e.g., "Flea Life" vs "Flealife")
            expected_title_norm.replace(' ', '') in track_name_norm.replace(' ', '') or
            track_name_norm.replace(' ', '') in expected_title_norm.replace(' ', '') or
            # Fuzzy match for typos (e.g., "Hanuted" vs "Haunted")
            self._strings_similar(expected_title, track_name)
        )

        # Check if artist matches any of the Spotify artists
        # Normalize spotify artists too
        spotify_artists_norm = [self._normalize_for_comparison(sa) for sa in spotify_artists]

        # Check for substring match or exact match
        artist_match = any(
            expected_artist_norm in sa or sa in expected_artist_norm
            for sa in spotify_artists_norm
        )

        # Also check if all Spotify artists combined match (for multi-artist tracks)
        all_artists = ', '.join(spotify_artists_norm)
        if not artist_match:
            artist_match = expected_artist_norm in all_artists or all_artists in expected_artist_norm

        # Try fuzzy match on artist for typo tolerance
        if not artist_match:
            artist_match = any(self._strings_similar(expected_artist, sa) for sa in spotify_artists)

        # Try removing trailing numbers from both sides (e.g., "TimeMachine1958" vs "TimeMachine1985")
        if not artist_match:
            # Split expected artist by separators (handles multi-artist tracks)
            expected_parts = [p.strip() for p in re.split(r'[,&]', expected_artist_norm)]

            for expected_part in expected_parts:
                # Extract core artist name by removing trailing digits
                expected_core = re.sub(r'\d+$', '', expected_part).strip()

                for sa in spotify_artists_norm:
                    spotify_core = re.sub(r'\d+$', '', sa).strip()
                    # Check if core names match (must be substantial, not just 1-2 chars)
                    if len(expected_core) > 3 and len(spotify_core) > 3:
                        if expected_core == spotify_core or expected_core in spotify_core or spotify_core in expected_core:
                            artist_match = True
                            break

                if artist_match:
                    break

        return title_match and artist_match

    def search_track(self, track: TrackInfo, episode_date: Optional[str] = None) -> Optional[Tuple[str, str, List[str]]]:
        """Search for a track on Spotify with multiple fallback strategies

        Args:
            track: TrackInfo object with artist, title, and optional variant
            episode_date: Optional episode publication date (ISO format) for smarter cache invalidation

        Returns:
            Tuple of (track_id, track_name, artist_names) if found, None otherwise
            Track is verified to match search criteria before returning
        """
        # Build search key for caching
        search_key = self._make_search_key(track.artist, track.title, track.variant)

        # Check cache first
        if search_key in self.state.tracks:
            cached = self.state.tracks[search_key]

            # Check if this is a cache hit (has track id)
            if cached.id:
                self._log_api_call("CACHE_HIT", f"{track.artist} - {track.title}")
                return (cached.id, cached.name, cached.artists)

            # This is a cached miss - decide if we should re-search
            # If force_search_missing is True, always search
            if not self.force_search_missing and cached.timestamp:
                try:
                    cached_time = datetime.fromisoformat(cached.timestamp)
                    age = datetime.now() - cached_time

                    # For episodes older than 1 year, trust the cached miss (track likely doesn't exist)
                    if episode_date:
                        try:
                            ep_date = datetime.fromisoformat(episode_date)
                            episode_age = datetime.now() - ep_date
                            if episode_age > timedelta(days=365):
                                # Old episode - trust the cached miss
                                self._log_api_call("CACHE_MISS", f"{track.artist} - {track.title} (old episode, cached)")
                                return None
                        except (ValueError, TypeError):
                            pass  # Invalid episode date, fall through to standard cache logic

                    # For recent episodes, use 1 day cache
                    if age < timedelta(days=1):
                        self._log_api_call("CACHE_MISS", f"{track.artist} - {track.title} (cached)")
                        return None
                    else:
                        # Miss is stale, search again
                        self._log_api_call("CACHE_MISS_EXPIRED", f"{track.artist} - {track.title}")
                except (ValueError, TypeError):
                    # Invalid timestamp format, search again
                    pass

        try:
            client = self._get_search_client()

            # Strategy 1: Field filters (most precise)
            query_parts = [f'track:"{track.title}"', f'artist:"{track.artist}"']
            if track.variant:
                query_parts[0] = f'track:"{track.title} {track.variant}"'
            query = " ".join(query_parts)

            self._log_api_call("SEARCH", f"Strategy 1: {query[:60]}...")
            results = client.search(q=query, type='track', limit=5)

            # Try to find a matching track in the results
            for track_data in results['tracks']['items']:
                if self._verify_track_match(track_data, track.artist, track.title):
                    track_id = track_data['id']
                    track_name = track_data['name']
                    artists = [artist['name'] for artist in track_data['artists']]

                    # Cache the result with timestamp
                    self.state.tracks[search_key] = SpotifyTrackCache(
                        id=track_id,
                        name=track_name,
                        artists=artists
                    )
                    self._save_state(tracks_only=True)

                    return (track_id, track_name, artists)

            # Strategy 2: Simple search without field filters (more flexible)
            if track.variant:
                simple_query = f"{track.artist} {track.title} {track.variant}"
            else:
                simple_query = f"{track.artist} {track.title}"

            self._log_api_call("SEARCH", f"Strategy 2: {simple_query[:60]}...")
            results = client.search(q=simple_query, type='track', limit=10)

            for track_data in results['tracks']['items']:
                if self._verify_track_match(track_data, track.artist, track.title):
                    track_id = track_data['id']
                    track_name = track_data['name']
                    artists = [artist['name'] for artist in track_data['artists']]

                    # Cache the result with timestamp
                    self.state.tracks[search_key] = SpotifyTrackCache(
                        id=track_id,
                        name=track_name,
                        artists=artists
                    )
                    self._save_state(tracks_only=True)

                    return (track_id, track_name, artists)

            # Strategy 3: Aggressive normalization (remove apostrophes, shorten title)
            # This handles cases like "Funk D'Void" → "Funk D Void" and "Flea Life" → "Flea"
            artist_clean = track.artist.replace("'", " ")
            title_words = track.title.split()
            # Try just the first word of the title
            title_first = title_words[0] if title_words else track.title

            aggressive_query = f"{artist_clean} {title_first}"
            self._log_api_call("SEARCH", f"Strategy 3: {aggressive_query[:60]}...")
            results = client.search(q=aggressive_query, type='track', limit=15)

            for track_data in results['tracks']['items']:
                if self._verify_track_match(track_data, track.artist, track.title):
                    track_id = track_data['id']
                    track_name = track_data['name']
                    artists = [artist['name'] for artist in track_data['artists']]

                    # Cache the result with timestamp
                    self.state.tracks[search_key] = SpotifyTrackCache(
                        id=track_id,
                        name=track_name,
                        artists=artists
                    )
                    self._save_state(tracks_only=True)

                    return (track_id, track_name, artists)

            # Strategy 4: Swap artist and title (sometimes RSS has them backwards)
            # Only try if both artist and title are substantial (not just a single word)
            if ' ' in track.artist or ' ' in track.title or len(track.artist) < 15:
                swap_query = f"{track.title} {track.artist}"
                self._log_api_call("SEARCH", f"Strategy 4: {swap_query[:60]}...")
                results = client.search(q=swap_query, type='track', limit=10)

                for track_data in results['tracks']['items']:
                    # Verify with swapped artist/title
                    if self._verify_track_match(track_data, track.title, track.artist):
                        track_id = track_data['id']
                        track_name = track_data['name']
                        artists = [artist['name'] for artist in track_data['artists']]

                        console.print(f"[yellow]  Note: Found with swapped artist/title[/yellow]")

                        # Cache the result with timestamp
                        self.state.tracks[search_key] = SpotifyTrackCache(
                            id=track_id,
                            name=track_name,
                            artists=artists
                        )
                        self._save_state(tracks_only=True)

                        return (track_id, track_name, artists)

            # No matching track found - cache the miss with timestamp
            console.print(f"[yellow]⚠ Track not found:[/yellow] {track.artist} - {track.title}")
            if track.variant:
                console.print(f"  [dim]Variant: {track.variant}[/dim]")

            # Cache miss to avoid re-searching for 1 day
            self.state.tracks[search_key] = SpotifyTrackCache()
            self._save_state(tracks_only=True)

            return None

        except Exception as e:
            console.print(f"[red]Error searching track {track.artist} - {track.title}: {e}[/red]")

            # Cache miss to avoid re-searching for 1 day
            self.state.tracks[search_key] = SpotifyTrackCache()
            self._save_state(tracks_only=True)

            return None

    def sync_episode_playlist(
        self,
        episode: Episode,
        playlist_format: str = "TGL {id}: {title}",
        playlist_description: str = "Tracks from {id}: {title}"
    ) -> bool:
        """Create or update a playlist for a single episode

        Args:
            episode: Episode to create playlist for
            playlist_format: Format string for playlist name
                {id} = episode ID (e.g., "E390")
                {title} = episode title
            playlist_description: Format string for playlist description
                {id} = episode ID (e.g., "E390")
                {title} = episode title

        Returns:
            True if successful, False otherwise
        """
        if not episode.tracklist:
            console.print(f"[yellow]Episode {episode.episode_id} has no tracklist[/yellow]")
            return False

        # Generate playlist name and description
        playlist_name = playlist_format.format(
            id=episode.episode_id,
            title=episode.title
        )
        playlist_desc = playlist_description.format(
            id=episode.episode_id,
            title=episode.title
        )

        playlist_key = f"episode:{episode.episode_id}"

        console.print(f"\n[cyan]Processing episode playlist:[/cyan] {playlist_name}")
        console.print(f"[dim]Episode: {episode.episode_id} - {episode.title}[/dim]")
        console.print(f"[dim]Tracks: {len(episode.tracklist)}[/dim]\n")

        # Search for all tracks
        found_tracks = []
        missing_tracks = []

        console.print(f"[cyan]Searching for tracks on Spotify...[/cyan]")
        for i, track in enumerate(episode.tracklist, 1):
            result = self.search_track(track, episode_date=episode.published)
            if result:
                track_id, track_name, artists = result
                found_tracks.append((track_id, track, track_name, artists))
                console.print(f"  [{i}/{len(episode.tracklist)}] [green]✓[/green] {track.artist} - {track.title}")
            else:
                missing_tracks.append(track)
                console.print(f"  [{i}/{len(episode.tracklist)}] [red]✗[/red] {track.artist} - {track.title}")

        console.print(f"\n[green]✓[/green] Found {len(found_tracks)}/{len(episode.tracklist)} tracks on Spotify")

        if missing_tracks:
            console.print(f"[yellow]⚠[/yellow] {len(missing_tracks)} tracks not found:\n")
            for track in missing_tracks[:5]:
                console.print(f"  • {track.artist} - {track.title}")
            if len(missing_tracks) > 5:
                console.print(f"  ... and {len(missing_tracks) - 5} more")

        if not found_tracks:
            console.print("\n[red]✗ No tracks found on Spotify[/red]")
            return False

        # Get track IDs
        track_ids = [t[0] for t in found_tracks]

        if self.dry_run:
            console.print(f"\n[yellow]Dry run mode - playlist would be created/updated but no changes made[/yellow]")
            return True

        # Create or update playlist
        console.print(f"\n[cyan]Syncing playlist...[/cyan]")

        # Check if we've already created this playlist
        playlist_id = None
        if playlist_key in self.state.playlists:
            playlist_id = self.state.playlists[playlist_key].id

        # Verify playlist still exists on Spotify
        if playlist_id:
            try:
                client = self._get_user_client()
                client.playlist(playlist_id)
            except:
                # Playlist doesn't exist anymore
                console.print(f"[yellow]Playlist no longer exists, will create new one[/yellow]")
                playlist_id = None

        # Get existing tracks in playlist
        existing_track_ids = set()
        if playlist_id:
            existing_track_ids = set(self.state.playlists[playlist_key].tracks)

        # Create playlist if it doesn't exist
        if not playlist_id:
            console.print(f"[cyan]Creating new playlist:[/cyan] {playlist_name}")
            client = self._get_user_client()
            self._log_api_call("CREATE_PLAYLIST", f"{playlist_name}")
            playlist = client.user_playlist_create(
                user=self._user_id,
                name=playlist_name,
                public=True,
                description=playlist_desc
            )
            playlist_id = playlist['id']
            console.print(f"[green]✓[/green] Created playlist")
        else:
            # Update playlist name and description if they don't match
            client = self._get_user_client()
            current_playlist = client.playlist(playlist_id)
            needs_update = False

            if current_playlist['name'] != playlist_name:
                console.print(f"[yellow]Updating playlist name:[/yellow] {current_playlist['name']} → {playlist_name}")
                needs_update = True

            if current_playlist['description'] != playlist_desc:
                console.print(f"[yellow]Updating playlist description[/yellow]")
                needs_update = True

            if needs_update:
                self._log_api_call("UPDATE_PLAYLIST", f"{playlist_name}")
                client.playlist_change_details(
                    playlist_id,
                    name=playlist_name,
                    description=playlist_desc
                )
                console.print(f"[green]✓[/green] Updated playlist details")

        # Determine which tracks to add (not already in playlist)
        tracks_to_add = [tid for tid in track_ids if tid not in existing_track_ids]

        if tracks_to_add:
            console.print(f"[cyan]Adding {len(tracks_to_add)} new tracks to playlist...[/cyan]")
            client = self._get_user_client()

            # Add in batches of 100
            batch_size = 100
            for i in range(0, len(tracks_to_add), batch_size):
                batch = tracks_to_add[i:i + batch_size]
                self._log_api_call("ADD_TRACKS", f"{len(batch)} tracks")
                client.playlist_add_items(playlist_id, batch)

            console.print(f"[green]✓[/green] Added {len(tracks_to_add)} tracks")
        else:
            console.print(f"[yellow]Playlist already up to date[/yellow]")

        # Upload cover art if needed
        from .cover import COVER_VERSION
        current_cover_version = self.state.playlists.get(playlist_key, SpotifyPlaylist(id="", name="")).cover_version
        if current_cover_version != COVER_VERSION:
            self._upload_cover(playlist_id, episode.episode_id)

        # Update state
        self.state.playlists[playlist_key] = SpotifyPlaylist(
            id=playlist_id,
            name=playlist_name,
            tracks=track_ids,
            cover_version=COVER_VERSION
        )
        self._save_state()

        playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
        console.print(f"\n[bold green]✓ Done![/bold green] Playlist: [link={playlist_url}]{playlist_url}[/link]\n")

        return True

    def sync_year_playlist(
        self,
        year: int,
        episodes: List[Episode],
        playlist_format: str = "The {year} Sound of The Guestlist by Fear of Tigers",
        playlist_description: str = "All tracks from The Guestlist episodes published in {year}"
    ) -> bool:
        """Create or update a playlist for all tracks from a specific year

        Tracks are ordered by last appearance (most recent first), same as --all.

        Args:
            year: Year to create playlist for
            episodes: List of all episodes (will be filtered by year)
            playlist_format: Format string for playlist name ({year} is replaced)
            playlist_description: Format string for playlist description ({year} is replaced)

        Returns:
            True if successful, False otherwise
        """
        # Filter episodes for this year
        year_episodes = [ep for ep in episodes if ep.year == year and ep.tracklist]

        if not year_episodes:
            console.print(f"[yellow]No episodes with tracklists found for {year}[/yellow]")
            return False

        # Sort episodes by date (most recent first) for determining last appearance
        episodes_sorted = sorted(year_episodes, key=lambda e: e.published, reverse=True)

        # Generate playlist name and description
        playlist_name = playlist_format.format(year=year)
        playlist_desc = playlist_description.format(year=year)
        playlist_key = f"year:{year}"

        console.print(f"\n[cyan]Processing year playlist:[/cyan] {playlist_name}")
        console.print(f"[dim]Year: {year}[/dim]")
        console.print(f"[dim]Episodes: {len(year_episodes)}[/dim]")

        # First pass: collect all unique tracks by artist|title (to minimize searches)
        # Also track the most recent episode date for each track (for smart caching)
        unique_by_name = {}  # track_key -> (TrackInfo, most_recent_date)
        total_tracks = 0
        for ep in episodes_sorted:  # Already sorted by date, most recent first
            for track in ep.tracklist:
                total_tracks += 1
                track_key = f"{track.artist.lower()}|{track.title.lower()}"
                if track_key not in unique_by_name:
                    # First occurrence is most recent (episodes_sorted is most recent first)
                    unique_by_name[track_key] = (track, ep.published)

        console.print(f"[dim]Tracks by name: {len(unique_by_name)} (from {total_tracks} total)[/dim]\n")

        # Search for all tracks and build map: track_key -> spotify_id
        track_key_to_spotify_id = {}
        missing_track_keys = set()

        console.print(f"[cyan]Searching for tracks on Spotify...[/cyan]")
        for i, (track_key, (track, episode_date)) in enumerate(unique_by_name.items(), 1):
            result = self.search_track(track, episode_date=episode_date)
            if result:
                track_id, track_name, artists = result
                track_key_to_spotify_id[track_key] = track_id
                console.print(f"  [{i}/{len(unique_by_name)}] [green]✓[/green] {track.artist} - {track.title}")
            else:
                missing_track_keys.add(track_key)
                console.print(f"  [{i}/{len(unique_by_name)}] [red]✗[/red] {track.artist} - {track.title}")

        # Second pass: track last appearance by Spotify ID
        # spotify_id -> (TrackInfo, last_published_date)
        spotify_appearances = {}

        for ep in episodes_sorted:
            for track in ep.tracklist:
                track_key = f"{track.artist.lower()}|{track.title.lower()}"
                # Skip tracks we couldn't find on Spotify
                if track_key in missing_track_keys:
                    continue

                spotify_id = track_key_to_spotify_id.get(track_key)
                if spotify_id and spotify_id not in spotify_appearances:
                    # First time seeing this Spotify ID = most recent appearance
                    spotify_appearances[spotify_id] = (track, ep.published)

        console.print(f"\n[green]✓[/green] Found {len(spotify_appearances)} unique tracks on Spotify (from {len(unique_by_name)} searches)")

        # Order tracks by last appearance date (most recent first)
        ordered_tracks = [(spotify_id, track, date) for spotify_id, (track, date) in spotify_appearances.items()]
        ordered_tracks.sort(key=lambda x: x[2], reverse=True)

        # Extract track IDs in order
        track_ids = [spotify_id for spotify_id, _, _ in ordered_tracks]

        # Build missing tracks list for display
        missing_tracks = [unique_by_name[tk][0] for tk in missing_track_keys]

        if missing_tracks:
            console.print(f"[yellow]⚠[/yellow] {len(missing_tracks)} tracks not found:\\n")
            for track in missing_tracks[:5]:
                console.print(f"  • {track.artist} - {track.title}")
            if len(missing_tracks) > 5:
                console.print(f"  ... and {len(missing_tracks) - 5} more")

        if not track_ids:
            console.print(f"\n[red]✗ No tracks found on Spotify[/red]")
            return False

        if self.dry_run:
            console.print(f"\n[yellow]Dry run mode - playlist would be created/updated but no changes made[/yellow]")
            return True

        # Create or update playlist
        console.print(f"\n[cyan]Syncing playlist...[/cyan]")

        # Check if we've already created this playlist
        playlist_id = None
        if playlist_key in self.state.playlists:
            playlist_id = self.state.playlists[playlist_key].id

        # Verify playlist still exists on Spotify
        if playlist_id:
            try:
                client = self._get_user_client()
                client.playlist(playlist_id)
            except:
                console.print(f"[yellow]Playlist no longer exists, will create new one[/yellow]")
                playlist_id = None

        # Get existing tracks in playlist (as list to preserve order)
        existing_track_ids = []
        if playlist_id:
            existing_track_ids = self.state.playlists[playlist_key].tracks

        # Create playlist if it doesn't exist
        if not playlist_id:
            console.print(f"[cyan]Creating new playlist:[/cyan] {playlist_name}")
            client = self._get_user_client()
            self._log_api_call("CREATE_PLAYLIST", f"{playlist_name}")
            playlist = client.user_playlist_create(
                user=self._user_id,
                name=playlist_name,
                public=True,
                description=playlist_desc
            )
            playlist_id = playlist['id']
            console.print(f"[green]✓[/green] Created playlist")
        else:
            # Update playlist name and description if they don't match
            client = self._get_user_client()
            current_playlist = client.playlist(playlist_id)
            needs_update = False

            if current_playlist['name'] != playlist_name:
                console.print(f"[yellow]Updating playlist name:[/yellow] {current_playlist['name']} → {playlist_name}")
                needs_update = True

            if current_playlist['description'] != playlist_desc:
                console.print(f"[yellow]Updating playlist description[/yellow]")
                needs_update = True

            if needs_update:
                self._log_api_call("UPDATE_PLAYLIST", f"{playlist_name}")
                client.playlist_change_details(
                    playlist_id,
                    name=playlist_name,
                    description=playlist_desc
                )
                console.print(f"[green]✓[/green] Updated playlist details")

        # Check if we need to update the playlist
        existing_set = set(existing_track_ids)
        new_set = set(track_ids)

        # Check if there are new tracks or if order has changed
        has_new_tracks = bool(new_set - existing_set)
        order_changed = existing_track_ids != track_ids

        needs_update = has_new_tracks or order_changed

        if needs_update:
            if has_new_tracks:
                new_count = len(new_set - existing_set)
                console.print(f"[cyan]Updating playlist: {new_count} new track(s)[/cyan]")
            if order_changed and not has_new_tracks:
                console.print(f"[cyan]Reordering playlist tracks[/cyan]")

            client = self._get_user_client()

            # Replace all tracks with the correct order
            # Spotify API limit: 100 tracks per request
            batch_size = 100

            # First batch replaces, subsequent batches append
            if track_ids:
                first_batch = track_ids[:batch_size]
                self._log_api_call("REPLACE_TRACKS", f"{len(first_batch)} tracks")
                client.playlist_replace_items(playlist_id, first_batch)

                # Add remaining tracks in batches
                for i in range(batch_size, len(track_ids), batch_size):
                    batch = track_ids[i:i + batch_size]
                    self._log_api_call("ADD_TRACKS", f"{len(batch)} tracks")
                    client.playlist_add_items(playlist_id, batch)

            console.print(f"[green]✓[/green] Playlist updated ({len(track_ids)} tracks)")
        else:
            console.print(f"[yellow]Playlist already up to date[/yellow]")

        # Upload cover art if needed
        from .cover import COVER_VERSION
        current_cover_version = self.state.playlists.get(playlist_key, SpotifyPlaylist(id="", name="")).cover_version
        if current_cover_version != COVER_VERSION:
            self._upload_cover(playlist_id, str(year))

        # Update state
        self.state.playlists[playlist_key] = SpotifyPlaylist(
            id=playlist_id,
            name=playlist_name,
            tracks=track_ids,
            cover_version=COVER_VERSION
        )
        self._save_state()

        playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
        console.print(f"\n[bold green]✓ Done![/bold green] Playlist: [link={playlist_url}]{playlist_url}[/link]\n")

        return True

    def sync_all_playlist(
        self,
        episodes: List[Episode],
        playlist_format: str = "The Sound of The Guestlist by Fear of Tigers",
        playlist_description: str = "All tracks from every episode of The Guestlist podcast"
    ) -> bool:
        """Create or update a playlist with ALL tracks from all episodes

        Tracks are deduplicated and ordered by last appearance (most recent first).

        Args:
            episodes: List of all episodes
            playlist_format: Name for the playlist
            playlist_description: Description for the playlist

        Returns:
            True if successful, False otherwise
        """
        # Filter episodes with tracklists
        episodes_with_tracks = [ep for ep in episodes if ep.tracklist]

        if not episodes_with_tracks:
            console.print(f"[yellow]No episodes with tracklists found[/yellow]")
            return False

        # Sort episodes by date (most recent first) for determining last appearance
        episodes_sorted = sorted(episodes_with_tracks, key=lambda e: e.published, reverse=True)

        playlist_name = playlist_format
        playlist_desc = playlist_description
        playlist_key = "all"

        console.print(f"\n[cyan]Processing all-tracks playlist:[/cyan] {playlist_name}")
        console.print(f"[dim]Episodes: {len(episodes_with_tracks)}[/dim]")

        # First pass: collect all unique tracks by artist|title (to minimize searches)
        # Also track the most recent episode date for each track (for smart caching)
        unique_by_name = {}  # track_key -> (TrackInfo, most_recent_date)
        total_tracks = 0

        for ep in episodes_sorted:  # Already sorted by date, most recent first
            for track in ep.tracklist:
                total_tracks += 1
                track_key = f"{track.artist.lower()}|{track.title.lower()}"
                if track_key not in unique_by_name:
                    # First occurrence is most recent (episodes_sorted is most recent first)
                    unique_by_name[track_key] = (track, ep.published)

        console.print(f"[dim]Tracks by name: {len(unique_by_name)} (from {total_tracks} total)[/dim]\n")

        # Search for all tracks and build map: track_key -> spotify_id
        track_key_to_spotify_id = {}
        missing_track_keys = set()

        console.print(f"[cyan]Searching for tracks on Spotify...[/cyan]")
        for i, (track_key, (track, episode_date)) in enumerate(unique_by_name.items(), 1):
            result = self.search_track(track, episode_date=episode_date)
            if result:
                track_id, track_name, artists = result
                track_key_to_spotify_id[track_key] = track_id
                console.print(f"  [{i}/{len(unique_by_name)}] [green]✓[/green] {track.artist} - {track.title}")
            else:
                missing_track_keys.add(track_key)
                console.print(f"  [{i}/{len(unique_by_name)}] [red]✗[/red] {track.artist} - {track.title}")

        # Second pass: track last appearance by Spotify ID
        # spotify_id -> (TrackInfo, last_published_date)
        spotify_appearances = {}

        for ep in episodes_sorted:
            for track in ep.tracklist:
                track_key = f"{track.artist.lower()}|{track.title.lower()}"
                # Skip tracks we couldn't find on Spotify
                if track_key in missing_track_keys:
                    continue

                spotify_id = track_key_to_spotify_id.get(track_key)
                if spotify_id and spotify_id not in spotify_appearances:
                    # First time seeing this Spotify ID = most recent appearance
                    spotify_appearances[spotify_id] = (track, ep.published)

        console.print(f"\n[green]✓[/green] Found {len(spotify_appearances)} unique tracks on Spotify (from {len(unique_by_name)} searches)")

        # Order tracks by last appearance date (most recent first)
        ordered_tracks = [(spotify_id, track, date) for spotify_id, (track, date) in spotify_appearances.items()]
        ordered_tracks.sort(key=lambda x: x[2], reverse=True)

        # Extract track IDs in order
        found_tracks = [spotify_id for spotify_id, _, _ in ordered_tracks]

        # Build missing tracks list for display
        missing_tracks = [unique_by_name[tk][0] for tk in missing_track_keys]

        if missing_tracks:
            console.print(f"[yellow]⚠[/yellow] {len(missing_tracks)} tracks not found:\\n")
            for track in missing_tracks[:5]:
                console.print(f"  • {track.artist} - {track.title}")
            if len(missing_tracks) > 5:
                console.print(f"  ... and {len(missing_tracks) - 5} more")

        if not found_tracks:
            console.print(f"\n[red]✗ No tracks found on Spotify[/red]")
            return False

        if self.dry_run:
            console.print(f"\n[yellow]Dry run mode - playlist would be created/updated but no changes made[/yellow]")
            return True

        # Create or update playlist
        console.print(f"\n[cyan]Syncing playlist...[/cyan]")

        # Check if we've already created this playlist
        playlist_id = None
        if playlist_key in self.state.playlists:
            playlist_id = self.state.playlists[playlist_key].id

        # Verify playlist still exists on Spotify
        if playlist_id:
            try:
                client = self._get_user_client()
                client.playlist(playlist_id)
            except:
                console.print(f"[yellow]Playlist no longer exists, will create new one[/yellow]")
                playlist_id = None

        # Get existing tracks in playlist (as list to preserve order)
        existing_track_ids = []
        if playlist_id:
            existing_track_ids = self.state.playlists[playlist_key].tracks

        # Create playlist if it doesn't exist
        if not playlist_id:
            console.print(f"[cyan]Creating new playlist:[/cyan] {playlist_name}")
            client = self._get_user_client()
            self._log_api_call("CREATE_PLAYLIST", f"{playlist_name}")
            playlist = client.user_playlist_create(
                user=self._user_id,
                name=playlist_name,
                public=True,
                description=playlist_desc
            )
            playlist_id = playlist['id']
            console.print(f"[green]✓[/green] Created playlist")
        else:
            # Update playlist name and description if they don't match
            client = self._get_user_client()
            current_playlist = client.playlist(playlist_id)
            needs_update = False

            if current_playlist['name'] != playlist_name:
                console.print(f"[yellow]Updating playlist name:[/yellow] {current_playlist['name']} → {playlist_name}")
                needs_update = True

            if current_playlist['description'] != playlist_desc:
                console.print(f"[yellow]Updating playlist description[/yellow]")
                needs_update = True

            if needs_update:
                self._log_api_call("UPDATE_PLAYLIST", f"{playlist_name}")
                client.playlist_change_details(
                    playlist_id,
                    name=playlist_name,
                    description=playlist_desc
                )
                console.print(f"[green]✓[/green] Updated playlist details")

        # Check if we need to update the playlist
        existing_set = set(existing_track_ids)
        new_set = set(found_tracks)

        # Check if there are new tracks or if order has changed
        has_new_tracks = bool(new_set - existing_set)
        order_changed = existing_track_ids != found_tracks

        needs_update = has_new_tracks or order_changed

        if needs_update:
            if has_new_tracks:
                new_count = len(new_set - existing_set)
                console.print(f"[cyan]Updating playlist: {new_count} new track(s)[/cyan]")
            if order_changed and not has_new_tracks:
                console.print(f"[cyan]Reordering playlist tracks[/cyan]")

            client = self._get_user_client()

            # Replace all tracks with the correct order
            # Spotify API limit: 100 tracks per request
            batch_size = 100

            # First batch replaces, subsequent batches append
            if found_tracks:
                first_batch = found_tracks[:batch_size]
                self._log_api_call("REPLACE_TRACKS", f"{len(first_batch)} tracks")
                client.playlist_replace_items(playlist_id, first_batch)

                # Add remaining tracks in batches
                for i in range(batch_size, len(found_tracks), batch_size):
                    batch = found_tracks[i:i + batch_size]
                    self._log_api_call("ADD_TRACKS", f"{len(batch)} tracks")
                    client.playlist_add_items(playlist_id, batch)

            console.print(f"[green]✓[/green] Playlist updated ({len(found_tracks)} tracks)")
        else:
            console.print(f"[yellow]Playlist already up to date[/yellow]")

        # Upload cover art if needed (no text for all-tracks playlist)
        from .cover import COVER_VERSION
        current_cover_version = self.state.playlists.get(playlist_key, SpotifyPlaylist(id="", name="")).cover_version
        if current_cover_version != COVER_VERSION:
            self._upload_cover(playlist_id, None)

        # Update state
        self.state.playlists[playlist_key] = SpotifyPlaylist(
            id=playlist_id,
            name=playlist_name,
            tracks=found_tracks,  # Store in order
            cover_version=COVER_VERSION
        )
        self._save_state()

        playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
        console.print(f"\n[bold green]✓ Done![/bold green] Playlist: [link={playlist_url}]{playlist_url}[/link]\n")

        return True
