# Guestlistr

TGL (The Guestlist) Podcast CLI Tool - Manage episodes, tracklists, and Spotify playlists

A comprehensive CLI tool to fetch, parse, search, and manage podcast episodes and their tracklists. Import tracks to Spotify, download episodes, and search through your podcast archive with full-text search.

## ✨ Features

- 📻 **Episode Management**: Fetch and cache podcast episodes from Patreon RSS feed
- 🎵 **Smart Tracklist Parsing**: Automatically parse tracklists with variant detection (remixes, features, extended mixes)
- 🔍 **Full-Text Search**: Search across episodes, tracks, and artists using Whoosh
- 🎧 **Download Episodes**: Save episode audio files locally
- 🎼 **Spotify Integration**: Import tracklists to Spotify playlists with state management
- 📊 **Intelligent Filtering**: Filter by year, episode type (TGL/BONUS), and more
- 💾 **Smart Caching**: Auto-refresh stale data, incremental updates, failed track retry logic

## 🚀 Quick Start

### Installation

```bash
# Clone and install
git clone <repo-url>
cd guestlistr

# Using uv (recommended)
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -e .

# Or using pip
pip install -e .
```

### Configuration

Create a `.env` file:

```bash
# Patreon RSS Feed
TGL_PATREON_RSS_URL=https://www.patreon.com/rss/your-creator?auth=your-token

# Spotify API (from https://developer.spotify.com/dashboard)
TGL_SPOTIFY_CLIENT_ID=your_client_id
TGL_SPOTIFY_CLIENT_SECRET=your_client_secret
TGL_SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback

# Playlist Name
TGL_SPOTIFY_PLAYLIST_NAME=guestlistr
```

> ⚠️ **Important**: Use `127.0.0.1` not `localhost` for the Spotify redirect URI (Spotify blocks localhost)

## 📖 Usage

### List Episodes

```bash
# List all episodes
tgl list

# Filter by year
tgl list --year 2023

# Show only TGL episodes
tgl list --tgl

# Show only BONUS episodes
tgl list --bonus
```

### Episode Details

```bash
# Show episode details (clickable IDs!)
tgl info E390
tgl show B05  # Bonus episode

# Supported ID formats:
# - Plain: "390"
# - E prefix: "E390"
# - B prefix: "B05"
```

### Search

```bash
# Search episodes by title, description, or tracks
tgl search "house music"
tgl search LAU
tgl search "Fabrizio Mammarella"
```

### Download

```bash
# Download episode audio
tgl download E390
```

### Spotify Import

```bash
# Import all episodes
tgl spotify

# Import last 10 episodes
tgl spotify -n 10

# Import specific year
tgl spotify --year 2023

# Dry run (no Spotify ops)
tgl spotify --dryrun

# Force refresh (ignore cache)
tgl spotify --force-refresh
```

### Refresh Cache

```bash
# Refresh episode metadata
tgl refresh
```

## 🏗️ Project Structure

```
guestlistr/
├── pyproject.toml              # Dependencies & config
├── README.md
├── .env.example
├── src/guestlistr/
│   ├── __init__.py            # Package exports
│   ├── cli.py                 # CLI commands
│   ├── models.py              # Pydantic models
│   ├── fetcher.py             # RSS & tracklist parser
│   ├── cache.py               # Metadata cache
│   ├── search.py              # Whoosh search index
│   ├── state.py               # State management
│   └── spotify.py             # Spotify integration
└── tests/
    └── test_parser.py         # Unit tests (27 tests)
```

## 🎯 Key Features

### Smart Tracklist Parsing

- **Multiple Formats**: `# Artist - Track`, `1. Artist - Track`, `Artist - Track`
- **Variant Detection**: Remixes, features, extended mixes
- **Special Prefixes**: `RECORD OF THE WEEK`, `FROM THE BLOGS`, `TRACK OF THE WEEK`
- **Prose Filtering**: Automatically excludes descriptive text
- **Smart Detection**: Distinguishes artist names from prose using capitalization

**Example parsed tracks:**
- `Prospa - Love Songs (feat. Kosmo Kint)` → Artist: "Prospa", Title: "Love Songs", Variant: "feat. Kosmo Kint"
- `Tuba Rex - The Magnetic Empire (Pianopoli Remix)` → Variant: "Pianopoli Remix"

### Episode Types

- **TGL Episodes** (E prefix): E1, E390, etc.
- **BONUS Episodes** (B prefix): B01, B05, etc.

### Intelligent Caching

- **Auto-refresh**: Cache expires after 1 hour
- **Search Index**: Whoosh full-text index for fast searches
- **State Management**: Tracks processed episodes and failed tracks
- **Retry Logic**: Failed tracks retried after 7 days (max 5 attempts)
- **Incremental Saves**: State saved after each episode

**Cache files:**
- `.cache/episodes.json` - Episode metadata
- `.cache/search_index/` - Whoosh search index
- `.guestlistr_state.json` - Spotify processing state

### Search Capabilities

Full-text search powered by Whoosh:
- Search episode titles (3x boost)
- Search track artists (5x boost)
- Search track titles (2x boost)
- Search episode descriptions

Results show:
- Relevance score
- Episode type (TGL/BONUS)
- Clickable episode IDs
- Match context (which field matched)

## 🧪 Development

### Running Tests

```bash
# All tests
pytest tests/

# With coverage
pytest tests/ --cov=guestlistr

# Verbose
pytest tests/ -v
```

### Test Coverage

27 unit tests covering:
- Episode ID parsing (E390, B05, plain numbers)
- Tracklist parsing with variants
- Edge cases from production (E340, E64, E75, E61)
- Prose filtering
- HTML entity handling
- Special prefixes

### Tech Stack

- **Pydantic** - Data validation & settings
- **Rich** - Beautiful terminal output
- **Typer** - CLI framework
- **Whoosh** - Full-text search
- **Spotipy** - Spotify API
- **feedparser** - RSS parsing
- **pytest** - Testing

## 📝 Migration from Script

The project was migrated from a single-file uv script (`tgl.py`) to a proper Python package:

**Benefits:**
- ✅ Centralized dependencies in `pyproject.toml`
- ✅ Modular code organization
- ✅ Proper testing infrastructure
- ✅ Installable as a package (`pip install -e .`)
- ✅ Entry point CLI command (`tgl`)

**Old way:** `uv run tgl.py list`
**New way:** `tgl list`

## 🐛 Troubleshooting

### "No episodes found"
- Check `.env` file exists and has correct `TGL_PATREON_RSS_URL`
- Run `tgl refresh` to rebuild cache

### "Track not found on Spotify"
- Some tracks may not be available on Spotify
- Different spelling/naming
- Regional restrictions
- The tool will retry failed tracks after 7 days

### Authentication Issues
- Delete `.cache` file
- Run `tgl spotify` again to re-authorize

### Import Errors
- Make sure you've installed the package: `uv pip install -e .`
- Activate virtual environment: `source .venv/bin/activate`

## 📄 License

MIT

---

**Made with ❤️ for podcast and music lovers**
