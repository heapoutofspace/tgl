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

# Optional - Spotify playlist formats (see .env.example for all options)
# export TGL_SPOTIFY_EPISODE_PLAYLIST_FORMAT="TGL {id}: {title}"
# export TGL_SPOTIFY_YEAR_PLAYLIST_FORMAT="The {year} Sound of The Guestlist"
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

# Spotify Playlist Configuration (see .env.example for all options)
# TGL_SPOTIFY_EPISODE_PLAYLIST_FORMAT=TGL {id}: {title}
# TGL_SPOTIFY_YEAR_PLAYLIST_FORMAT=The {year} Sound of The Guestlist
# TGL_SPOTIFY_ALL_PLAYLIST_FORMAT=The Sound of The Guestlist

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
# List all episodes (shows ✅ for downloaded episodes)
tgl list

# Filter by year
tgl list --year 2023

# Show only TGL episodes
tgl list --tgl

# Show only BONUS episodes
tgl list --bonus

# Show only summary statistics
tgl list --summary
```

The list command displays:
- **✅** Download status indicator for episodes you have locally
- Episode type (🎧 TGL or 🎁 BONUS)
- Track count, date, and duration
- Clickable episode IDs that open the Patreon post

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

### Download Episodes

```bash
# Download single episode
tgl download E390

# Download multiple episodes
tgl download E390 E391 B01

# Download all TGL episodes
tgl download --tgl

# Download all BONUS episodes
tgl download --bonus

# Download all episodes
tgl download --all

# Force re-download (even if file exists)
tgl download E390 --force
```

**Features:**
- Files saved with **correct extensions** (.mp3, .wav, .m4a, .aac, .flac, etc.)
- **Verifies file sizes** match RSS feed before skipping downloads
- **Extracts duration** metadata from audio files automatically
- **Concurrent downloads** (up to 5 at once) for faster batch downloads
- **Detailed error reporting** with clickable Patreon links if downloads fail

### Spotify Import

```bash
# Sync specific year or episode
tgl spotify 2024        # Year 2024
tgl spotify E390        # Episode E390
tgl spotify B01         # BONUS episode B01
tgl spotify 2024 E390   # Multiple years/episodes

# Create playlists for all years
tgl spotify --years

# Create all-tracks playlist
tgl spotify --all

# Update all tracked playlists
tgl spotify --sync

# Dry run (preview only, no changes)
tgl spotify --dry-run

# Show Spotify API calls
tgl spotify --verbose
```

**Playlist Configuration:**

Customize playlist titles and descriptions with placeholders:

```bash
# Episode playlists (placeholders: {id}, {title})
tgl config set spotify_episode_playlist_format "TGL {id}: {title}"
tgl config set spotify_episode_playlist_description "Tracks from {id}: {title}"

# Year playlists (placeholder: {year})
tgl config set spotify_year_playlist_format "The {year} Sound of The Guestlist"
tgl config set spotify_year_playlist_description "All tracks from {year}"

# All-tracks playlist
tgl config set spotify_all_playlist_format "The Sound of The Guestlist"
tgl config set spotify_all_playlist_description "All tracks from every episode"
```

**Features:**
- **Auto-updates** playlist titles and descriptions if configuration changes
- **State tracking** prevents duplicate imports
- **Failed track retry** with 7-day cooldown (max 5 attempts)
- **Batch operations** for efficient API usage

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
├── tgl/
│   ├── __init__.py            # Package exports
│   ├── cli.py                 # CLI commands & config management
│   ├── config.py              # Configuration & settings
│   ├── models.py              # Pydantic data models
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
