# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TGL (The Guestlist) is a CLI tool for managing podcast episodes, tracklists, and Spotify playlists from Patreon RSS feeds. Built as a Python package using modern tooling (uv, pyproject.toml).

### Key Dependencies
- **typer** - CLI framework
- **rich** - Beautiful terminal output
- **pydantic-settings** - Type-safe configuration
- **whoosh** - Full-text search engine (file-based index)
- **feedparser** - RSS feed parsing
- **spotipy** - Spotify API client
- **httpx** - Async HTTP client for downloads
- **mutagen** - Audio metadata extraction
- **platformdirs** - Platform-specific directories
- **tomli-w** - TOML config file writing

## Quick Start

```bash
# Install package
uv pip install -e .

# Fetch and cache all episodes from RSS feed
tgl update

# List all episodes
tgl list

# Show episodes from a specific year
tgl list --year 2024

# Download episodes
tgl download E390 E391

# Import tracklists to Spotify
tgl spotify 2024
```

## Configuration Management

TGL uses a layered configuration system with priority:

1. **Environment variables** (highest priority)
2. **Config file** (platform-specific location)
3. **`.env` file** (project directory)
4. **Default values** (lowest priority)

### Platform-Specific Directories

Managed by `paths.py` using platformdirs:

- **macOS**: `~/Library/Application Support/TGL/`
- **Linux**: `~/.local/share/TGL/` and `~/.config/TGL/`
- **Windows**: `C:\Users\<user>\AppData\Local\TGL\`

Config file locations:
- **macOS**: `~/Library/Application Support/TGL/config.toml`
- **Linux**: `~/.config/TGL/config.toml`
- **Windows**: `C:\Users\<user>\AppData\Local\TGL\config.toml`

### Configuration Variables

All variables support both prefixed (`TGL_`) and non-prefixed formats for backward compatibility.

**Required:**
- `TGL_PATREON_RSS_URL` - RSS feed URL with auth token (keep private)

**Optional (Spotify):**
- `TGL_SPOTIFY_CLIENT_ID` - From Spotify Developer Dashboard
- `TGL_SPOTIFY_CLIENT_SECRET` - From Spotify Developer Dashboard
- `TGL_SPOTIFY_REDIRECT_URI` - OAuth redirect (default: `http://127.0.0.1:8888/callback`)

**Optional (Spotify Playlists):**
- `TGL_SPOTIFY_EPISODE_PLAYLIST_FORMAT` - Episode playlist title format (default: `TGL {id}: {title}`)
- `TGL_SPOTIFY_EPISODE_PLAYLIST_DESCRIPTION` - Episode playlist description (default: `Tracks from {id}: {title}`)
- `TGL_SPOTIFY_YEAR_PLAYLIST_FORMAT` - Year playlist title format
- `TGL_SPOTIFY_YEAR_PLAYLIST_DESCRIPTION` - Year playlist description
- `TGL_SPOTIFY_ALL_PLAYLIST_FORMAT` - All-tracks playlist title
- `TGL_SPOTIFY_ALL_PLAYLIST_DESCRIPTION` - All-tracks playlist description

**Note:** Use `127.0.0.1` not `localhost` - Spotify blocks localhost in OAuth settings.

### Variable Name Priority

If both prefixed and non-prefixed versions exist, the `TGL_` prefixed version takes priority.

## Architecture

### Module Structure

#### `config.py` - Configuration & Settings
- **Settings class** using `pydantic_settings.BaseSettings`
- Auto-loads from config file, .env, and environment
- **Backward compatible**: Accepts both `TGL_` prefixed and non-prefixed variable names
- Type-safe configuration with Field descriptions and `AliasChoices`
- Global `settings` instance available throughout

#### `paths.py` - Platform-Specific Paths
- Uses `platformdirs` for OS-appropriate locations
- Manages config file, data directory, cache directory, state files
- Supports custom data directory via `TGL_DATA_DIR` environment variable

### Episode Types & IDs

All episodes are classified into two types with distinct ID formats:

**TGL Episodes** (🎧 cyan):
- Main "The Guestlist" podcast episodes
- ID format: `E{number}` (e.g., E390, E174, E212)
- Episode number extracted from title or inferred from surrounding episodes
- Always expected to have tracklists

**BONUS Episodes** (🎁 magenta):
- Special content: "From The Crates", Fear of Tigers releases, re-ups, interviews, trailers
- ID format: `B{sequential}` (e.g., B01, B05, B127)
- Sequential numbering in chronological order
- May or may not have tracklists

### Pydantic Models

**TrackInfo** (`models.py`):
```python
class TrackInfo(BaseModel):
    artist: str
    title: str
    variant: Optional[str] = None  # e.g., "Original Mix", "Remix", "feat. Artist"
```

**Episode** (`models.py`):
```python
class Episode(BaseModel):
    id: int  # Numeric ID (for backward compatibility)
    episode_id: Optional[str] = None  # Formatted ID like "E390" or "B01"
    title: str  # Clean title (after colon)
    full_title: str  # Original title from RSS
    description: str  # Raw HTML description
    description_text: Optional[str] = None  # Cleaned text before tracklist
    tracklist: Optional[List[TrackInfo]] = None
    published: str  # ISO date format
    year: Optional[int] = None
    link: str  # RSS item link (unique identifier)
    audio_url: Optional[str] = None
    audio_size: Optional[int] = None  # Audio file size in bytes (from RSS feed)
    episode_type: str = 'TGL'  # 'TGL' or 'BONUS'
    duration: Optional[str] = None  # Episode duration (e.g., "1:23:45")
```

### Core Classes

#### PatreonPodcastFetcher (`fetcher.py`)
- **Episode Classification** (`classify_episode_type`): Detects TGL vs BONUS based on title patterns
- **Episode ID Assignment** (`assign_episode_id`): Assigns E-prefix or B-prefix IDs
- **RSS Parsing** (`fetch_episodes`): Fetches from Patreon RSS, classifies all episodes, extracts audio_size
- **Tracklist Parsing** (`_parse_structured_tracklist`): Extracts tracks from descriptions

#### TracklistParser (`fetcher.py`)
Enhanced parsing with prose detection:
- Strips HTML, handles entities
- Parses "Artist - Track" format with `#` or number prefixes
- **Prose filtering**: Skips lines with common English words (if, the, you, etc.)
- **Length limits**: Skips lines >120 chars (likely prose)
- **Special markers**: Handles "RECORD OF THE WEEK:", "FROM THE CRATES:", etc.
- **Date detection**: Skips date patterns like "31st December - "
- Removes duplicates, handles "(Original Mix)" suffix
- **Variant extraction**: Detects remixes, features, extended mixes

#### SpotifyPlaylistManager (`spotify.py`)
- OAuth authentication via spotipy
- Track search with fuzzy matching
- Playlist creation/update with **auto-update** of titles and descriptions
- Batch operations (100 tracks/request limit)
- Duplicate prevention
- State tracking for processed episodes and failed tracks

#### MetadataCache (`cache.py`)
- Persistent episode metadata in platform-specific location
- Fast lookups by ID or year
- Pydantic serialization for type safety
- Auto-refresh when stale (1 hour)

#### StateManager (`state.py`)
Production vs dryrun state files in platform-specific directories

Tracks:
- Processed episodes (prevents re-processing)
- Failed tracks with retry logic (7-day wait, 5 max attempts)
- Cumulative statistics
- Playlist state for sync operations

### CLI Commands

**update/fetch** - Update episode metadata cache from RSS feed
```bash
tgl update
```

**list** - List episodes with download status indicators
```bash
tgl list [--year YEAR] [--tgl] [--bonus] [--summary]
```
- Shows ✅ for downloaded episodes
- Displays episode type (🎧 TGL, 🎁 BONUS)
- Clickable episode IDs that open Patreon posts

**info/show** - Display detailed episode information
```bash
tgl info EPISODE_ID
```

**search** - Full-text search across episodes, tracks, and artists
```bash
tgl search "query"
```

**download** - Download episode audio files with verification
```bash
tgl download EPISODE_ID [--tgl] [--bonus] [--all] [--force]
```
**Features:**
- Files saved with correct extensions (.mp3, .wav, .m4a, .aac, .flac, etc.)
- Verifies file sizes match RSS feed before skipping
- Extracts duration metadata from audio files (supports all formats via mutagen)
- Concurrent downloads (up to 5 at once)
- Detailed error reporting with clickable Patreon links

**spotify** - Import tracklists to Spotify playlists
```bash
tgl spotify [IDENTIFIERS...] [--years] [--all] [--sync] [--dry-run] [--verbose]
```
- Identifiers can be years (2024) or episode IDs (E390, B01)
- `--years`: Create playlists for all years
- `--all`: Create all-tracks playlist
- `--sync`: Update all tracked playlists
- Auto-updates playlist titles and descriptions based on config

**config** - Configuration management
```bash
tgl config init              # Interactive setup
tgl config show              # Show current config
tgl config set KEY VALUE     # Set config value
tgl config unset KEY         # Remove config value
tgl config edit              # Edit config file
tgl config path [--all]      # Show config/data paths
```

## Key Implementation Details

### Tracklist Parsing Improvements

The parser filters out prose and non-track content:

1. **Line length check** (>120 chars = likely prose)
2. **Header detection** ("interview", "mixtape", "poetry corner", "tribute to")
3. **Prose word detection** (filters lines with "if", "you", "the", "this", "with", etc.)
4. **Special format handling** ("RECORD OF THE WEEK 2: Artist - Track")

### Episode Number Inference

For TGL episodes without explicit numbers:
1. Extract numbers from surrounding episodes
2. Infer based on chronological position
3. Examples: "Last of the New Fire 2024" → E373

### Audio File Handling

#### File Extensions
- Cache stores files with correct extensions from URL (.mp3, .wav, .m4a, etc.)
- Destination files use same extension as cached files
- `_get_cached_audio_path()` extracts extension from URL path

#### Duration Extraction
- Uses `mutagen.File()` for auto-detection of audio format
- Supports MP3, WAV, M4A/AAC, FLAC, AIFF, and other formats
- Extracts duration even for skipped files (if missing)
- Check uses `audio is not None` not `if audio` (WAV objects are falsy)

#### File Size Verification
- Audio file sizes stored from RSS feed enclosures
- Verifies local file size matches RSS feed before skipping
- Re-downloads files if size mismatch detected
- Ensures downloaded files are complete

### Rich Terminal Output

All commands use Rich for beautiful output:
- Progress bars with spinners
- Colored tables with clickable links
- Episode type icons (🎧 for TGL, 🎁 for BONUS)
- Type-specific colors (cyan for TGL, magenta for BONUS)
- Download status indicators (✅ for downloaded)

### Spotify Authentication

First run opens browser for OAuth. Credentials cached in `spotify.json`. Delete oauth_token field to re-authorize.

**Important:** Redirect URI must be `http://127.0.0.1:8888/callback` (not localhost).

### Spotify Playlist Auto-Update

When syncing playlists:
- Checks if current title/description matches config
- Auto-updates if different
- Uses `playlist_change_details()` API call

## Development Notes

### Adding Dependencies

Update `pyproject.toml` in the `[project.dependencies]` section.

### Rich Markup

When repeating characters:
- ✅ Correct: `"[bold cyan]" + "═" * 60`
- ❌ Wrong: `"[bold cyan]═" * 60` (prints each character on new line)

### Error Handling

All commands use typer.Exit(1) for clean error exits with proper status codes.

### Testing

Unit tests are in `tests/test_parser.py` and cover:
- Episode ID parsing (E390, B05, plain numbers)
- Track parsing logic (including variant extraction)
- Edge cases from production issues (E340 prose filtering, etc.)

Run tests:
```bash
pytest tests/
```

Test coverage includes:
- **27 test cases** covering ID parsing, track parsing, and model validation
- Examples from real episodes that had parsing issues
- Edge cases: HTML entities, special prefixes, prose filtering, duplicate detection
- Variant extraction: remixes, features, extended mixes

## Cache Management

Episode metadata and search index are stored in platform-specific directories:
- `episodes.json` - Episode metadata with timestamp (auto-refreshes after 1 hour)
- `search_index/` - Whoosh full-text search index (automatically rebuilt when metadata refreshes)
- `audio/` - Cached audio files (hard-linked to episodes directory)

State files (in data directory):
- `spotify.json` - Spotify state, OAuth tokens, and playlist tracking

Episodes directory structure:
- `episodes/tgl/` - TGL episode audio files
- `episodes/bonus/` - BONUS episode audio files

### Search Index

The search functionality uses **Whoosh**, a pure Python full-text search library. The index includes:
- Episode titles (3x boost)
- Artist names (5x boost for highest relevance)
- Track titles (2x boost)
- Episode descriptions (1x boost)

The index is automatically rebuilt when:
- Running `tgl update` command
- Metadata cache is refreshed due to staleness (> 1 hour old)
- Index doesn't exist when searching

Search queries support multiple words without quotes:
```bash
tgl search Fabrizio Mammarella  # No quotes needed
tgl search house music          # Multiple words work naturally
```

All cache and data files are stored in platform-specific directories (use `tgl config path --all` to view).
