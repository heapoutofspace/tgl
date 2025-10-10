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

**Required:** Patreon RSS URL
**Optional:** Spotify credentials (only needed for `tgl spotify` command)

On first run, TGL will automatically guide you through the setup process. You only need to provide your Patreon RSS URL to get started.

TGL supports multiple configuration methods (in priority order):

1. **Environment variables** (highest priority)
2. **Config file** (platform-specific location)
3. **`.env` file** (project directory)
4. **Default values** (lowest priority)

#### Interactive Setup

```bash
# Initialize or reconfigure TGL
tgl config init
```

#### Manual Configuration

**Option 1: Config file (recommended)**

```bash
# Show config file location
tgl config path

# Edit config file
tgl config edit

# Set individual values
tgl config set spotify_client_id your_client_id
tgl config set spotify_client_secret your_secret

# Show current config
tgl config show
```

Config file locations:
- **macOS**: `~/Library/Application Support/TGL/config.toml`
- **Linux**: `~/.config/TGL/config.toml`
- **Windows**: `C:\Users\<user>\AppData\Local\TGL\config.toml`

**Option 2: Environment variables**

```bash
# Required
export TGL_PATREON_RSS_URL=https://www.patreon.com/rss/your-creator?auth=your-token

# Optional - Spotify integration
export TGL_SPOTIFY_CLIENT_ID=your_client_id
export TGL_SPOTIFY_CLIENT_SECRET=your_client_secret
export TGL_SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
# export TGL_SPOTIFY_PLAYLIST_NAME="The Sound of The Guestlist by Fear of Tigers"  # default
```

**Option 3: .env file**

Create a `.env` file in the project directory:

```bash
# Patreon RSS Feed (REQUIRED)
TGL_PATREON_RSS_URL=https://www.patreon.com/rss/your-creator?auth=your-token

# Spotify API (OPTIONAL - only needed for 'tgl spotify' command)
# Create an app at: https://developer.spotify.com/dashboard
# TGL_SPOTIFY_CLIENT_ID=your_client_id
# TGL_SPOTIFY_CLIENT_SECRET=your_client_secret
# TGL_SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
# TGL_SPOTIFY_PLAYLIST_NAME=The Sound of The Guestlist by Fear of Tigers  # default

# Optional: Override data directory location
# TGL_DATA_DIR=/custom/path/to/data
```

> ⚠️ **Note**: Spotify credentials are optional. If configured, use `127.0.0.1` not `localhost` for the redirect URI (Spotify blocks localhost)

#### Custom Data Directory

You can override the data directory location using the `TGL_DATA_DIR` environment variable:

```bash
# Via environment variable
export TGL_DATA_DIR=/path/to/custom/data

# Or in .env file
echo "TGL_DATA_DIR=/path/to/custom/data" >> .env
```

**Note**: The config file location cannot be overridden and always uses platform-specific directories.

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

### Update Cache

```bash
# Update episode metadata
tgl update
```

### Configuration Management

```bash
# Initialize config with prompts
tgl config init

# Show current configuration
tgl config show

# Set a value
tgl config set spotify_playlist_name "My Playlist"

# Remove a value
tgl config unset spotify_playlist_name

# Edit config file in default editor
tgl config edit

# Show config file path
tgl config path

# Show all paths (data, cache, state)
tgl config path --all
```

## 🏗️ Project Structure

```
guestlistr/
├── pyproject.toml              # Dependencies & config
├── README.md
├── .env.example
├── src/tgl/
│   ├── __init__.py            # Package exports
│   ├── cli.py                 # CLI commands & config management
│   ├── models.py              # Pydantic models & settings
│   ├── paths.py               # Platform-specific paths
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
- **Platform-specific**: Uses OS-appropriate directories via platformdirs

**Data files** (stored in platform-specific directories):

Show all paths with: `tgl config path --all`

- **macOS**: `~/Library/Application Support/TGL/`
- **Linux**: `~/.local/share/TGL/`
- **Windows**: `C:\Users\<user>\AppData\Local\TGL\`

Files stored:
- `episodes.json` - Episode metadata cache
- `search_index/` - Whoosh full-text search index
- `spotify.json` - Spotify track cache, playlist state, and OAuth tokens

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
pytest tests/ --cov=tgl

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
- **platformdirs** - Platform-specific directories
- **tomli-w** - TOML config file writing
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
- Run `tgl update` to rebuild cache

### "Track not found on Spotify"
- Some tracks may not be available on Spotify
- Different spelling/naming
- Regional restrictions
- The tool will retry failed tracks after 7 days

### Authentication Issues
- OAuth tokens are stored in `spotify.json` (see path with `tgl config path --all`)
- To re-authorize: delete the `oauth_token` field from `spotify.json` or delete the entire file
- Run `tgl spotify` again to authorize with fresh credentials

### Import Errors
- Make sure you've installed the package: `uv pip install -e .`
- Activate virtual environment: `source .venv/bin/activate`

## 📄 License

MIT

---

**Made with ❤️ for podcast and music lovers**
