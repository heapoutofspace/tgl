# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a single-file Python script that extracts tracklists from Patreon podcast RSS feeds and creates Spotify playlists. The script uses `uv` for dependency management with inline PEP 723 script metadata.

## Running the Script

```bash
# Process all episodes (default)
uv run patreon_to_spotify.py

# Process only the last N episodes
uv run patreon_to_spotify.py -n 10

# Dry run mode (no Spotify operations)
uv run patreon_to_spotify.py -n 5 --dryrun
```

**Note:** `uv` automatically installs dependencies from the inline script metadata on first run.

## Environment Configuration

Required environment variables in `.env`:
- `PATREON_RSS_URL` - RSS feed URL with auth token (keep private)
- `SPOTIFY_CLIENT_ID` - From Spotify Developer Dashboard
- `SPOTIFY_CLIENT_SECRET` - From Spotify Developer Dashboard
- `SPOTIFY_REDIRECT_URI` - Should be `http://localhost:8888/callback`
- `SPOTIFY_PLAYLIST_NAME` - Name for the playlist (optional)

Copy `.env.example` to `.env` and fill in your credentials.

## Architecture

The script consists of three main classes:

### 1. PatreonPodcastFetcher
- Fetches podcast episodes from RSS feed using `requests` (not direct `feedparser.parse()` to handle SSL properly)
- Returns list of episodes with title, description, published date, and link
- Supports limiting to N most recent episodes

### 2. TracklistParser
- Strips HTML tags and unescapes HTML entities from episode descriptions
- Parses track listings in "Artist - Track" format using regex
- Filters out headers, URLs, and duplicate tracks
- Returns list of track dictionaries with artist, track, and search query

### 3. SpotifyPlaylistManager
- Handles Spotify authentication via spotipy OAuth
- Searches for tracks on Spotify
- Creates or updates playlists
- Avoids adding duplicate tracks
- Adds tracks in batches of 100 (Spotify API limit)

### Main Execution Flow
1. Parse CLI arguments (argparse)
2. Fetch episodes from RSS feed with Rich progress bar
3. Parse tracklists from episode descriptions
4. **If dryrun:** Show summary and exit
5. **If not dryrun:** Initialize Spotify, search tracks, create/update playlist

## Key Implementation Details

### Tracklist Parsing
The parser expects tracks in format: `# Artist - Track` or `Artist - Track`
- Removes leading `#` and numbers
- Skips lines containing tracklist headers or URLs
- Handles HTML entities (`&amp;` → `&`)
- Creates unique track keys to avoid duplicates

### SSL Handling
Uses `requests.get()` to fetch RSS feed content, then passes to `feedparser.parse()`. This avoids SSL certificate verification errors that occur with direct URL parsing.

### Rich Progress Bars
Progress display includes:
- SpinnerColumn
- TextColumn (description)
- BarColumn
- TaskProgressColumn (percentage)
- TimeRemainingColumn

### Dry Run Mode
When `--dryrun` is specified:
- Skips Spotify authentication entirely
- Shows first 10 parsed tracks
- Useful for debugging tracklist parsing without API calls

### Rich Markup in Console Output
When using Rich console.print() with repeated characters, use string concatenation not multiplication:
- **Correct:** `console.print("[bold cyan]" + "═" * 60)`
- **Wrong:** `console.print("[bold cyan]═" * 60)` - prints each character on separate line

## Debugging Tracklist Parsing

If tracks aren't being extracted:
1. Use `--dryrun` to see what's being parsed
2. Check the regex pattern in `TracklistParser.parse_tracklist()`
3. Common formats supported: `Artist - Track`, `1. Artist - Track`, `# Artist - Track`
4. Patterns skip lines with: "tracklist", "record of the week", "guestmix", URLs

## Spotify Authentication

First run opens browser for OAuth authorization. Credentials cached in `.cache` file. If auth issues occur, delete `.cache` and re-authorize.
