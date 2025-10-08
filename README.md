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
- `--year YYYY`: Filter episodes by specific year (e.g., 2024)
- `--years`: List all available years from podcast episodes
- `--dryrun`: Run in dry run mode (parse episodes and tracks without creating/updating Spotify playlist)
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
- Running the script multiple times with the same year updates the existing playlist

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
