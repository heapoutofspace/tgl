# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TGL (The Guestlist) is a CLI tool for managing podcast episodes, tracklists, and Spotify playlists from Patreon RSS feeds. Built with Python using `uv` for dependency management (PEP 723 inline script metadata).

### Key Dependencies
- **typer** - CLI framework
- **rich** - Beautiful terminal output
- **pydantic-settings** - Type-safe configuration
- **whoosh** - Full-text search engine (file-based index)
- **feedparser** - RSS feed parsing
- **spotipy** - Spotify API client
- **requests** - HTTP library for RSS fetching

## Quick Start

```bash
# Fetch and cache all episodes from RSS feed
./tgl.py fetch

# List all episodes
./tgl.py list

# Show episodes from a specific year
./tgl.py list --year 2024

# Import tracklists to Spotify
./tgl.py spotify -n 50

# Dry run (no Spotify changes)
./tgl.py spotify -n 10 --dryrun

# Filter by year
./tgl.py spotify --year 2024
```

## Environment Configuration

Environment variables are managed via **pydantic-settings** and support both prefixed and non-prefixed formats for backward compatibility.

Required variables in `.env`:
- `TGL_PATREON_RSS_URL` or `PATREON_RSS_URL` - RSS feed URL with auth token (keep private)
- `TGL_SPOTIFY_CLIENT_ID` or `SPOTIFY_CLIENT_ID` - From Spotify Developer Dashboard
- `TGL_SPOTIFY_CLIENT_SECRET` or `SPOTIFY_CLIENT_SECRET` - From Spotify Developer Dashboard
- `TGL_SPOTIFY_REDIRECT_URI` or `SPOTIFY_REDIRECT_URI` - OAuth redirect (default: `http://127.0.0.1:8888/callback`)
- `TGL_SPOTIFY_PLAYLIST_NAME` or `SPOTIFY_PLAYLIST_NAME` - Playlist name (default: `TGL`)

**Note:** Use `127.0.0.1` not `localhost` - Spotify blocks localhost in OAuth settings.

### Variable Name Priority

If both prefixed and non-prefixed versions exist, the `TGL_` prefixed version takes priority. This allows gradual migration without breaking existing setups.

## Architecture

### Configuration (`tgl.py:47-94`)
- **Settings class** using `pydantic_settings.BaseSettings`
- Auto-loads from `.env` file
- **Backward compatible**: Accepts both `TGL_` prefixed and non-prefixed variable names
- Type-safe configuration with Field descriptions and `AliasChoices`
- Global `settings` instance available throughout

### Episode Types & IDs

All episodes are now classified into two types with distinct ID formats:

**TGL Episodes** (🎧 cyan):
- Main "The Guestlist" podcast episodes
- ID format: `E{number}` (e.g., E390, E174, E212)
- Episode number extracted from title or inferred from surrounding episodes
- Always expected to have tracklists

**OTHER Episodes** (⭐ magenta):
- Special content: "From The Crates", Fear of Tigers releases, re-ups, interviews, trailers
- ID format: `X{sequential}` (e.g., X01, X02, X03)
- Sequential numbering in chronological order
- May or may not have tracklists

### Pydantic Models

**TrackInfo** (`tgl.py:80-83`):
```python
class TrackInfo(BaseModel):
    artist: str
    title: str
```

**Episode** (`tgl.py:86-96`):
```python
class Episode(BaseModel):
    id: int  # Numeric ID (for backward compatibility)
    title: str  # Clean title (after colon)
    full_title: str  # Original title from RSS
    description: str  # Raw HTML description
    description_text: str  # Cleaned text before tracklist
    tracklist: Optional[List[TrackInfo]]
    published: str  # ISO date format
    year: Optional[int]
    link: str  # RSS item link (unique identifier)
    audio_url: Optional[str]
```

### Core Classes

#### PatreonPodcastFetcher (`tgl.py:165-407`)
- **Episode Classification** (`classify_episode_type`): Detects TGL vs OTHER based on title patterns
- **Episode ID Assignment** (`assign_episode_id`): Assigns E-prefix or X-prefix IDs
- **RSS Parsing** (`fetch_episodes`): Fetches from Patreon RSS, classifies all episodes
- **Tracklist Parsing** (`_parse_structured_tracklist`): Extracts tracks from descriptions

#### TracklistParser (`tgl.py:412-493`)
Enhanced parsing with prose detection:
- Strips HTML, handles entities
- Parses "Artist - Track" format with `#` or number prefixes
- **Prose filtering**: Skips lines with common English words (if, the, you, etc.)
- **Length limits**: Skips lines >120 chars (likely prose)
- **Special markers**: Handles "RECORD OF THE WEEK:", "FROM THE CRATES:", etc.
- **Date detection**: Skips date patterns like "31st December - "
- Removes duplicates, handles "(Original Mix)" suffix

#### SpotifyPlaylistManager (`tgl.py:501-597`)
- OAuth authentication via spotipy
- Track search with fuzzy matching
- Playlist creation/update
- Batch operations (100 tracks/request limit)
- Duplicate prevention

#### MetadataCache (`tgl.py:117-161`)
- Persistent episode metadata in `.tgl_cache.json`
- Fast lookups by ID or year
- Pydantic serialization for type safety

#### StateManager (`tgl.py:602-680`)
Production vs dryrun state files:
- **Production**: `.guestlistr_state.json`
- **Dryrun**: `.guestlistr_state_dryrun.json`

Tracks:
- Processed episodes (prevents re-processing)
- Failed tracks with retry logic (7-day wait, 5 max attempts)
- Cumulative statistics

### CLI Commands

**fetch** - Fetch and cache all episode metadata
```bash
./tgl.py fetch
```

**list** - List episodes with optional year filter
```bash
./tgl.py list [--year YEAR]
```

**years** - Show available years with episode counts
```bash
./tgl.py years
```

**show** - Display detailed episode information
```bash
./tgl.py show EPISODE_ID
```

**download** - Download episode audio file
```bash
./tgl.py download EPISODE_ID [--output PATH]
```

**spotify** - Import tracklists to Spotify
```bash
./tgl.py spotify [OPTIONS]
  -n, --episodes INT      Limit to N recent episodes
  --year INT              Filter by year
  --dryrun                Preview without Spotify changes
  --force-refresh         Bypass cache
  --use-cache             Use cache with year filter
```

## Key Implementation Details

### Tracklist Parsing Improvements

The parser now filters out prose and non-track content:

1. **Line length check** (>120 chars = likely prose)
2. **Header detection** ("interview", "mixtape", "poetry corner", "tribute to")
3. **Prose word detection** (filters lines with "if", "you", "the", "this", "with", etc.)
4. **Special format handling** ("RECORD OF THE WEEK 2: Artist - Track")

### Episode Number Inference

For TGL episodes without explicit numbers:
1. Extract numbers from surrounding episodes
2. Infer based on chronological position
3. Examples: "Last of the New Fire 2024" → E373, "Lo-Fi Belgrade Trailer" → X01

### Rich Terminal Output

All commands use Rich for beautiful output:
- Progress bars with spinners
- Colored tables with clickable links
- Episode type icons (🎧 for TGL, ⭐ for OTHER)
- Type-specific colors (cyan for TGL, magenta for OTHER)

### Spotify Authentication

First run opens browser for OAuth. Credentials cached in `.cache` file. Delete `.cache` to re-authorize.

**Important:** Redirect URI must be `http://127.0.0.1:8888/callback` (not localhost).

## Development Notes

### Adding Dependencies

Update the PEP 723 metadata at the top of `tgl.py`:
```python
# /// script
# dependencies = [
#   "package==version",
# ]
# ///
```

### Rich Markup

When repeating characters:
- ✅ Correct: `"[bold cyan]" + "═" * 60`
- ❌ Wrong: `"[bold cyan]═" * 60` (prints each character on new line)

### Error Handling

All commands use typer.Exit(1) for clean error exits with proper status codes.

### Testing

Unit tests are in `test_tgl.py` and cover:
- Episode ID parsing (E390, B05, plain numbers)
- Track parsing logic (including variant extraction)
- Edge cases from production issues (E340 prose filtering, etc.)

Run tests:
```bash
./test_tgl.py
```

Test coverage includes:
- **24 test cases** covering ID parsing, track parsing, and model validation
- Examples from real episodes that had parsing issues
- Edge cases: HTML entities, special prefixes, prose filtering, duplicate detection
- Variant extraction: remixes, features, extended mixes

Tests use pytest and include their own PEP 723 dependencies (including all tgl.py dependencies).

## Cache Management

Episode metadata and search index are stored in `.cache/` directory:
- `.cache/episodes.json` - Episode metadata with timestamp (auto-refreshes after 1 hour)
- `.cache/search_index/` - Whoosh full-text search index (automatically rebuilt when metadata refreshes)

State files (git-ignored):
- `.guestlistr_state.json` (production)
- `.guestlistr_state_dryrun.json` (dryrun mode)

### Search Index

The search functionality uses **Whoosh**, a pure Python full-text search library. The index is stored in `.cache/search_index/` and includes:
- Episode titles (3x boost)
- Artist names (5x boost for highest relevance)
- Track titles (2x boost)
- Episode descriptions (1x boost)

The index is automatically rebuilt when:
- Running `./tgl.py refresh` command
- Metadata cache is refreshed due to staleness (> 1 hour old)
- Index doesn't exist when searching

Search queries support multiple words without quotes:
```bash
./tgl.py search Fabrizio Mammarella  # No quotes needed
./tgl.py search house music          # Multiple words work naturally
```

All cache files are git-ignored.
