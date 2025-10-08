# Patreon Podcast to Spotify Playlist

Automatically extract tracklists from Patreon podcast episodes and create a Spotify playlist with all the tracks.

## Features

- Fetches all episodes from a Patreon podcast RSS feed
- Parses tracklists from episode show notes
- Searches for tracks on Spotify
- Creates or updates a Spotify playlist
- Avoids duplicate tracks
- Supports multiple tracklist formats

## Prerequisites

- [uv](https://docs.astral.sh/uv/) - Fast Python package installer
- A Patreon subscription with access to the podcast RSS feed
- A Spotify account
- Spotify API credentials

## Setup

### 1. Install uv

If you don't have uv installed:

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or with pip
pip install uv
```

### 2. Get Patreon RSS Feed URL

1. Log into your Patreon account
2. Go to the creator's page
3. Look for the podcast RSS feed URL (usually found in the podcast settings or by checking your podcast app)
4. The URL will look like: `https://www.patreon.com/rss/creator-name?auth=your-auth-token`

**Note:** The RSS feed URL includes an authentication token that's specific to your account. Keep it private.

### 3. Set Up Spotify API

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Log in with your Spotify account
3. Click "Create an App"
4. Fill in the app name and description (e.g., "Patreon to Spotify")
5. Once created, you'll see your **Client ID** and **Client Secret**
6. Click "Edit Settings"
7. Add `http://localhost:8888/callback` to the "Redirect URIs" and save

### 4. Configure Environment Variables

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and fill in your credentials:
   ```
   PATREON_RSS_URL=https://www.patreon.com/rss/your-creator?auth=your-token
   SPOTIFY_CLIENT_ID=your_spotify_client_id
   SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
   SPOTIFY_REDIRECT_URI=http://localhost:8888/callback
   SPOTIFY_PLAYLIST_NAME=DJ Patreon Mixes
   ```

## Usage

Run the script with uv:

```bash
# Process all episodes (default)
uv run patreon_to_spotify.py

# Process only the last 10 episodes
uv run patreon_to_spotify.py --episodes 10

# Or use the short form
uv run patreon_to_spotify.py -n 5

# List available years
uv run patreon_to_spotify.py --years

# Create playlist for a specific year
uv run patreon_to_spotify.py --year 2023
```

The first time you run it, uv will automatically install all dependencies defined in the script.

### Command Line Options

- `--episodes N` or `-n N`: Process only the N most recent episodes
- `--year YYYY`: Filter episodes by specific year (e.g., 2024) - **skips cache by default**
- `--years`: List all available years from podcast episodes
- `--use-cache`: Use cache even when filtering by year (e.g., `--year 2024 --use-cache`)
- `--per-episode`: Create individual playlists for each episode instead of one combined playlist
- `--playlist-prefix PREFIX`: Prefix for playlist names in per-episode mode (e.g., `"TGL - "`)
- `--dryrun`: Run in dry run mode (parse episodes and tracks without creating/updating Spotify playlist)
- `--show-cache`: Display cache statistics and exit
- `--clean-cache`: Remove failed tracks that exceeded max retry attempts (5)
- `--force-refresh`: Ignore cache and reprocess all episodes
- `--help` or `-h`: Show help message

### Dry Run Mode

Use `--dryrun` flag for debugging or testing without making changes to Spotify:

```bash
# Test parsing with the last 5 episodes without creating playlist
uv run patreon_to_spotify.py -n 5 --dryrun
```

In dry run mode, the script will:
- Fetch and parse episodes
- Extract tracklists
- Show a summary of tracks found
- **Skip** all Spotify operations (no authentication required)
- Use a separate cache file (`.guestlistr_state_dryrun.json`) that doesn't affect your main cache

### Year Filtering

You can organize your playlists by year:

```bash
# List all available years with episode counts
uv run patreon_to_spotify.py --years

# Create a playlist for 2023 episodes only
uv run patreon_to_spotify.py --year 2023

# Get the last 10 episodes from 2024
uv run patreon_to_spotify.py --year 2024 -n 10
```

**Key features:**
- When using `--year`, the playlist name automatically includes the year (e.g., "guestlistr 2023")
- The script fetches all episodes, filters by year, then applies the episode limit if specified
- Separate playlists are created for each year, making it easy to organize tracks chronologically
- **By default, `--year` skips the cache** and reprocesses all episodes for that year (useful for rebuilding year-specific playlists)
- Use `--use-cache` with `--year` if you want incremental updates instead of full reprocessing

### Per-Episode Playlists

Create individual Spotify playlists for each episode:

```bash
# Create individual playlists for each episode
uv run patreon_to_spotify.py --per-episode

# Process last 10 episodes as individual playlists
uv run patreon_to_spotify.py --per-episode -n 10

# Add a prefix to all playlist names for organization
uv run patreon_to_spotify.py --per-episode --playlist-prefix "TGL - "

# Combine with year filtering
uv run patreon_to_spotify.py --per-episode --year 2024
```

**Key features:**
- Creates one playlist per episode using the episode title as the playlist name
- Use `--playlist-prefix` to add a prefix (e.g., "TGL - E390: Love Songs" instead of "E390: Love Songs")
- Playlist names with the same prefix will group together in Spotify
- Cache automatically tracks which episodes have playlists - only creates playlists for new episodes
- Perfect for automated weekly runs: just add `--per-episode` to your cron job

**Organizing in Spotify:**
- The Spotify API doesn't support programmatic folder management
- All playlists with the same prefix will naturally group together in your library
- You can manually drag them into a folder in the Spotify app once
- New playlists will continue to be added (you may need to manually move them to the folder)

**Typical workflow:**
1. First run: `uv run patreon_to_spotify.py --per-episode --playlist-prefix "TGL - "` creates playlists for all episodes
2. In Spotify app: Create a folder and manually organize all "TGL - " playlists into it
3. Weekly automated runs: New episodes automatically get playlists created
4. Manually move new playlists to the folder as needed

### Smart Caching & Continuous Updates

The script automatically tracks processed episodes and failed tracks, making it perfect for regular weekly updates:

```bash
# Run weekly to add new episodes - only processes new content
uv run patreon_to_spotify.py

# Check what's been cached
uv run patreon_to_spotify.py --show-cache

# Force reprocess everything (ignores cache)
uv run patreon_to_spotify.py --force-refresh

# Clean up failed tracks that exceeded retry limit
uv run patreon_to_spotify.py --clean-cache
```

**How it works:**
- **Episode Tracking**: Already-processed episodes are skipped automatically
- **Failed Track Retry**: Tracks not found on Spotify are retried after 7 days (perfect for newly released tracks!)
- **Max Attempts**: Each track is retried up to 5 times before being marked as permanently unavailable
- **State File**: Everything is stored in `.guestlistr_state.json` (automatically created)
- **Incremental Saving**: State is saved after each episode is processed, so you can safely cancel (Ctrl+C) and resume later

**Typical workflow:**
1. First run: Processes all episodes, finds most tracks, records failures
2. Weekly runs: Only processes new episodes, automatically retries old failures
3. Result: Continuous playlist enrichment as new episodes release and tracks become available!

### First Run

On the first run, a browser window will open asking you to authorize the app with your Spotify account. After authorizing, you'll be redirected to a localhost URL. The script will automatically capture the authorization and continue.

### What Happens

1. The script fetches all episodes from the Patreon RSS feed
2. Parses tracklists from each episode's show notes
3. Searches for each track on Spotify
4. Creates a new playlist (or updates existing one) with all found tracks
5. Prints a summary and playlist URL

### Subsequent Runs

Run the script again anytime to fetch new episodes and add new tracks to your playlist. The script will:
- Only add tracks that aren't already in the playlist
- Preserve existing tracks
- Add newly found tracks from new episodes

## Troubleshooting

### "No tracks found"

The tracklist parser supports common formats like:
- `Artist - Track`
- `1. Artist - Track`
- `Artist: Track`

If tracks aren't being found, check the show notes format and adjust the regex patterns in `TracklistParser` class if needed.

### "Track not found on Spotify"

Some tracks might not be available on Spotify or the search might not find them due to:
- Different spelling or naming
- Track not available in your region
- Remixes/edits that aren't on Spotify

The script will continue and add the tracks it can find.

### Authentication Issues

If you get Spotify authentication errors:
1. Delete the `.cache` file in the project directory
2. Run the script again
3. Re-authorize when prompted

## Customization

### Playlist Name

Change the `SPOTIFY_PLAYLIST_NAME` in your `.env` file.

### Tracklist Parsing

Edit the `TracklistParser` class in `patreon_to_spotify.py` to add custom regex patterns for your specific tracklist format.

### Search Accuracy

Modify the `search_track` method in `SpotifyPlaylistManager` to adjust the search query or increase the number of results to check.

## License

MIT
