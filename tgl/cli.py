"""TGL CLI entry point and commands"""

# Configure multiprocessing FIRST before any other imports
# This is CRITICAL for faster-whisper to work properly with Textual TUI
#
# Background:
# - faster-whisper uses PyTorch which uses multiprocessing internally
# - Textual TUI creates special file descriptors for terminal I/O
# - Default 'spawn' method tries to pass these file descriptors to child processes
# - This causes "bad value(s) in fds_to_keep" error
#
# Solution:
# - Use 'fork' method instead of 'spawn'
# - Fork copies parent process memory without reinitializing everything
# - File descriptors are properly handled in forked processes
#
# MUST be set before importing torch, faster_whisper, or tgl modules
import multiprocessing as mp
try:
    if mp.get_start_method(allow_none=True) is None:
        mp.set_start_method('fork', force=True)
except RuntimeError:
    # Already set (shouldn't happen, but handle gracefully)
    pass

import typer
from typing import Optional, Annotated, List, Dict
from pathlib import Path
from collections import defaultdict
import os
import re
import subprocess
import tomllib
import tomli_w
import hashlib
import asyncio
from urllib.parse import urlparse, urlunparse
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text
import requests
import httpx

from tgl import (
    settings,
    MetadataCache,
    SearchIndex,
    PatreonPodcastFetcher,
    parse_episode_id,
    Track,
    paths,
    TranscriptionCache,
    transcribe_audio,
)

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
    # Check if RSS URL is configured (skip check for config commands)
    if ctx.invoked_subcommand != "config" and not settings.patreon_rss_url:
        console.print("\n[yellow]⚠ TGL is not yet configured[/yellow]\n")
        console.print("[dim]Let's set up your configuration...[/dim]\n")
        config_init()
        console.print("[green]✓ Configuration complete![/green]")
        console.print("[dim]Please run your command again to use TGL.[/dim]\n")
        raise typer.Exit(0)

    if ctx.invoked_subcommand is None:
        # No command provided, show custom help
        console.print("\n[bold cyan]" + "═" * 70)
        console.print("[bold cyan]TGL - The Guestlist Podcast CLI Tool")
        console.print("[bold cyan]" + "═" * 70 + "\n")

        console.print("[bold]Available Commands:[/bold]\n")

        console.print("  [cyan]list[/cyan]                 List all episodes")
        console.print("  [cyan]get[/cyan]                  Get episode metadata")
        console.print("  [cyan]set[/cyan]                  Set episode metadata field")
        console.print("  [cyan]search[/cyan]               Search episodes by title, description, or tracks")
        console.print("  [cyan]download[/cyan]             Download an episode audio file")
        console.print("  [cyan]transcribe[/cyan]           Transcribe episodes using Whisper AI")
        console.print("  [cyan]spotify[/cyan]              Import tracklists to Spotify playlist")
        console.print("  [cyan]update[/cyan]               Update episode metadata from RSS feed")
        console.print("  [cyan]doctor[/cyan]               Diagnose metadata and track mapping issues")
        console.print("  [cyan]config[/cyan]               Manage TGL configuration\n")

        console.print("[bold]Examples:[/bold]\n")
        console.print("  [dim]# List all episodes[/dim]")
        console.print("  [green]tgl.py list[/green]\n")

        console.print("  [dim]# List only TGL episodes from 2023[/dim]")
        console.print("  [green]tgl.py list --year 2023 --tgl[/green]\n")

        console.print("  [dim]# Show metadata for episode 390[/dim]")
        console.print("  [green]tgl.py get E390[/green]\n")

        console.print("  [dim]# Get specific field (supports GUIDs too)[/dim]")
        console.print("  [green]tgl.py get E390 title[/green]\n")

        console.print("  [dim]# Change episode metadata[/dim]")
        console.print("  [green]tgl.py set E390 title \"New Title\"[/green]\n")

        console.print("  [dim]# Search for episodes about house music[/dim]")
        console.print("  [green]tgl.py search \"house music\"[/green]\n")

        console.print("  [dim]# Transcribe an episode[/dim]")
        console.print("  [green]tgl.py transcribe E390[/green]\n")

        console.print("  [dim]# Update the episode cache[/dim]")
        console.print("  [green]tgl.py update[/green]\n")

        console.print("[dim]For detailed help on any command, use:[/dim]")
        console.print("  [green]tgl.py [command] --help[/green]\n")

# ═══════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════

def parse_episode_range(range_str: str, all_episodes: list) -> list:
    """Parse episode range like 'E100-E150' and return list of episode IDs

    Args:
        range_str: Range string like 'E100-E150' or 'E200-E250'
        all_episodes: List of all episodes to find matches in

    Returns:
        List of episode ID strings in the range
    """
    if '-' not in range_str:
        # Not a range, return as-is
        return [range_str]

    parts = range_str.split('-')
    if len(parts) != 2:
        # Invalid format, return as-is
        return [range_str]

    start_id, end_id = parts[0].strip().upper(), parts[1].strip().upper()

    # Extract prefix and numbers
    import re
    start_match = re.match(r'([A-Z]+)(\d+)', start_id)
    end_match = re.match(r'([A-Z]+)(\d+)', end_id)

    if not start_match or not end_match:
        # Invalid format, return as-is
        return [range_str]

    start_prefix, start_num = start_match.groups()
    end_prefix, end_num = end_match.groups()

    if start_prefix != end_prefix:
        # Different prefixes, invalid
        console.print(f"[yellow]Warning: Range '{range_str}' has different prefixes, skipping[/yellow]")
        return []

    start_num = int(start_num)
    end_num = int(end_num)

    if start_num > end_num:
        # Invalid range
        console.print(f"[yellow]Warning: Invalid range '{range_str}' (start > end), skipping[/yellow]")
        return []

    # Generate all IDs in range
    episode_ids = []
    for num in range(start_num, end_num + 1):
        episode_id = f"{start_prefix}{num}"
        # Check if episode exists
        episode, _ = find_episode_by_id_or_guid(all_episodes, episode_id)
        if episode:
            episode_ids.append(episode_id)

    return episode_ids


def find_episode_by_id_or_guid(episodes: list, identifier: str):
    """Find an episode by episode_id or guid

    Args:
        episodes: List of Episode objects
        identifier: Episode ID (e.g., E390, B05) or GUID

    Returns:
        Tuple of (episode, index) if found, (None, None) if not found
    """
    identifier_upper = identifier.upper()

    # Try to find by episode_id first
    for idx, ep in enumerate(episodes):
        if ep.episode_id == identifier_upper:
            return ep, idx

    # Try to find by guid
    for idx, ep in enumerate(episodes):
        if ep.guid == identifier:
            return ep, idx

    return None, None


# ═══════════════════════════════════════════════════════════════════════
# Commands
# ═══════════════════════════════════════════════════════════════════════

@app.command(name="update")
def update_cache():
    """Update episode metadata cache from RSS feed"""
    console.print("\n[bold cyan]" + "═" * 60)
    console.print("[bold cyan]Updating Episode Metadata")
    console.print("[bold cyan]" + "═" * 60 + "\n")

    cache = MetadataCache()
    fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)

    # Use refresh() method which handles deduplication and reclassification
    cache.refresh(fetcher)

    console.print(f"[bold green]✓ Done![/bold green] Cached {len(cache.episodes)} episodes")
    console.print("[bold cyan]" + "═" * 60 + "\n")

# Aliases for backward compatibility and convenience
@app.command(name="refresh", hidden=True)
def refresh_alias():
    """Alias for update command"""
    update_cache()

@app.command(name="fetch", hidden=True)
def fetch_alias():
    """Alias for update command"""
    update_cache()


@app.command()
def list(
    year: Optional[int] = typer.Option(None, "--year", help="Filter by year"),
    tgl: bool = typer.Option(False, "--tgl", help="Show only TGL episodes"),
    bonus: bool = typer.Option(False, "--bonus", help="Show only BONUS episodes"),
    summary: bool = typer.Option(False, "--summary", help="Show only summary statistics")
):
    """List all episodes with download status indicators (✅ = downloaded, - = not downloaded)"""
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
        # Sort TGL episodes by episode number (ascending)
        episodes.sort(key=lambda ep: ep.id)
    elif bonus and not tgl:
        episodes = [ep for ep in episodes if ep.episode_type == 'BONUS']
        # Reverse to show oldest first
        episodes = episodes[::-1]
    else:
        # Show all episodes, oldest first
        episodes = episodes[::-1]

    if not episodes:
        console.print(f"[yellow]No episodes found[/yellow]")
        raise typer.Exit(1)

    # Generate summary (used both for --summary and after table)
    def show_summary():
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
                tgl_val = year_stats[yr]['TGL']
                bonus_val = year_stats[yr]['BONUS']
                total = tgl_val + bonus_val
                total_tgl += tgl_val
                total_bonus += bonus_val
                overview_table.add_row(str(yr), str(tgl_val), str(bonus_val), str(total))

            # Add totals row
            overview_table.add_row(
                "[bold]Total[/bold]",
                f"[bold]{total_tgl}[/bold]",
                f"[bold]{total_bonus}[/bold]",
                f"[bold]{total_tgl + total_bonus}[/bold]"
            )

            console.print(overview_table)
        console.print()

    # If --summary flag is set, show summary and exit
    if summary:
        show_summary()
        return

    # Show episode list
    console.print()
    table = Table(title=f"TGL Episodes{f' ({year})' if year else ''}", show_header=True, header_style="bold cyan")
    table.add_column("Type", justify="center", width=4)
    table.add_column("ID", style="green", justify="right", width=6)
    table.add_column("Title", style="white", no_wrap=False, overflow="fold")
    table.add_column("Tracks", style="dim", justify="center", width=6)
    table.add_column("Date", style="yellow", width=12)
    table.add_column("Duration", style="cyan", justify="right", width=8)
    table.add_column("DL", style="dim", justify="center", width=3)

    for episode in episodes:
        # Create clickable episode ID
        clickable_id = Text(episode.episode_id)
        clickable_id.stylize(f"link {episode.link}")

        # Get episode type icon
        type_icon = "🎧" if episode.episode_type == "TGL" else "🎁"

        # Get track count
        track_count = str(len(episode.tracklist)) if episode.tracklist else "-"

        # Get duration
        duration = episode.duration if episode.duration else "-"

        # Check if audio file is downloaded
        if episode.episode_type == 'TGL':
            dest_dir = paths.tgl_episodes_dir
        else:
            dest_dir = paths.bonus_episodes_dir

        # Determine correct file extension from audio URL
        file_extension = '.mp3'  # default
        if episode.audio_url:
            cached_path = _get_cached_audio_path(episode)
            if cached_path:
                file_extension = cached_path.suffix or '.mp3'

        filename = f"{episode.episode_id} - {episode.title}{file_extension}"
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        dest_path = dest_dir / filename

        # Check with correct extension first, fall back to .mp3 for legacy files
        if dest_path.exists():
            download_status = "✅"
        elif file_extension != '.mp3':
            # Check if old .mp3 version exists
            legacy_filename = f"{episode.episode_id} - {episode.title}.mp3"
            legacy_filename = re.sub(r'[<>:"/\\|?*]', '', legacy_filename)
            legacy_path = dest_dir / legacy_filename
            download_status = "✅" if legacy_path.exists() else "-"
        else:
            download_status = "-"

        table.add_row(type_icon, clickable_id, episode.title, track_count, episode.published, duration, download_status)

    console.print(table)
    console.print(f"\n[dim]Total: {len(episodes)} episodes[/dim]")
    console.print(f"[dim]💡 Tip: Click on episode IDs to open in browser[/dim]")

    # Show summary at the bottom
    show_summary()


@app.command()
def get(
    episode_id: str = typer.Argument(..., help="Episode ID (e.g., E390, B05) or GUID"),
    field: Optional[str] = typer.Argument(None, help="Specific field to get (optional)"),
):
    """Get episode metadata

    Show all metadata for an episode, or a specific field if specified.
    Accepts episode IDs (E390, B05) or GUIDs.

    Examples:
        tgl get E390           # Show all metadata
        tgl get E390 title     # Show just the title
        tgl get B05 episode_type  # Show episode type
        tgl get 45962766       # Find by GUID
    """
    from .cache import MetadataCache
    import json

    cache = MetadataCache()
    episodes = cache.get_all_episodes()

    # Find the episode by ID or GUID
    episode, _ = find_episode_by_id_or_guid(episodes, episode_id)

    if not episode:
        console.print(f"[red]Episode {episode_id} not found[/red]")
        raise typer.Exit(1)

    # If field specified, show just that field
    if field:
        if not hasattr(episode, field):
            console.print(f"[red]Field '{field}' does not exist[/red]")
            console.print(f"[dim]Available fields: {', '.join(episode.model_fields.keys())}[/dim]")
            raise typer.Exit(1)

        value = getattr(episode, field)
        is_manual = field in episode.manual_overrides

        console.print(f"\n[bold]{episode.episode_id}[/bold] - [cyan]{field}[/cyan]:")
        # Handle different value types
        if value is None:
            console.print(f"  [dim]None[/dim]")
        elif isinstance(value, str):
            console.print(f"  {value}")
        elif isinstance(value, (int, float, bool)):
            console.print(f"  {value}")
        else:
            # For lists, dicts, sets, and other complex types
            console.print(json.dumps(value, indent=2, default=str))

        if is_manual:
            console.print(f"  [yellow]⚠ Manually overridden[/yellow]")
        console.print()
        return

    # Show all metadata
    console.print(f"\n[bold cyan]═══ Episode Metadata: {episode.episode_id} ═══[/bold cyan]\n")

    # Format fields nicely
    fields_to_show = [
        ('episode_id', 'Episode ID'),
        ('id', 'Internal ID'),
        ('title', 'Title'),
        ('episode_type', 'Type'),
        ('published', 'Published'),
        ('year', 'Year'),
        ('duration', 'Duration'),
        ('link', 'Patreon Link'),
        ('guid', 'RSS GUID'),
        ('audio_url', 'Audio URL'),
        ('audio_size', 'Audio Size (bytes)'),
    ]

    for field_name, label in fields_to_show:
        value = getattr(episode, field_name)
        is_manual = field_name in episode.manual_overrides
        manual_indicator = " [yellow]⚠[/yellow]" if is_manual else ""

        if value is not None:
            if field_name == 'link':
                console.print(f"[cyan]{label}:{manual_indicator}[/cyan] [link={value}]{value}[/link]")
            elif field_name == 'audio_url' and value:
                short_url = value[:60] + "..." if len(value) > 60 else value
                console.print(f"[cyan]{label}:{manual_indicator}[/cyan] {short_url}")
            else:
                console.print(f"[cyan]{label}:{manual_indicator}[/cyan] {value}")

    # Show tracklist count
    if episode.tracklist:
        is_manual = 'tracklist' in episode.manual_overrides
        manual_indicator = " [yellow]⚠[/yellow]" if is_manual else ""
        console.print(f"[cyan]Tracks:{manual_indicator}[/cyan] {len(episode.tracklist)}")

        # Show tracklist
        console.print(f"\n[bold cyan]Tracklist:[/bold cyan]")
        for i, track in enumerate(episode.tracklist, 1):
            track_text = f"{track.artist} - {track.title}"
            if track.variant:
                track_text += f" [dim]({track.variant})[/dim]"
            console.print(f"  {i:2d}. {track_text}")

    # Show manual overrides if any
    if episode.manual_overrides:
        console.print(f"\n[yellow]Manually overridden fields:[/yellow] {', '.join(sorted(episode.manual_overrides))}")

    console.print()


# Keep info and show as hidden aliases for backward compatibility
@app.command(name="info", hidden=True)
@app.command(name="show", hidden=True)
def info_alias(episode_id: str):
    """Alias for get command (deprecated)"""
    get(episode_id=episode_id, field=None)


@app.command()
def set(
    episode_id: str = typer.Argument(..., help="Episode ID (e.g., E390, B05) or GUID"),
    field: str = typer.Argument(..., help="Field to set"),
    value: str = typer.Argument(..., help="New value"),
):
    """Set episode metadata field manually

    Accepts episode IDs (E390, B05) or GUIDs.
    Changing episode_type will trigger ID recalculation.
    Changing episode_id requires valid format and checks for duplicates.

    Examples:
        tgl set E390 episode_type BONUS     # Change type to BONUS
        tgl set B05 episode_type TGL        # Change type to TGL
        tgl set E390 episode_id E395        # Change episode number
        tgl set E390 title "New Title"      # Change title
        tgl set 45962766 episode_type TGL   # Set by GUID
    """
    from .cache import MetadataCache
    from .models import parse_episode_id

    cache = MetadataCache()
    episodes = cache.get_all_episodes()

    # Find the episode by ID or GUID
    episode, episode_idx = find_episode_by_id_or_guid(episodes, episode_id)

    if not episode:
        console.print(f"[red]Episode {episode_id} not found[/red]")
        raise typer.Exit(1)

    # Validate field exists
    if field not in episode.model_fields:
        console.print(f"[red]Field '{field}' does not exist[/red]")
        console.print(f"[dim]Available fields: {', '.join(episode.model_fields.keys())}[/dim]")
        raise typer.Exit(1)

    # Restricted fields that can't be set manually
    restricted_fields = {'id', 'manual_overrides'}
    if field in restricted_fields:
        console.print(f"[red]Field '{field}' cannot be set manually[/red]")
        raise typer.Exit(1)

    old_value = getattr(episode, field)

    # Special handling for episode_type changes
    if field == 'episode_type':
        if value.upper() not in ['TGL', 'BONUS']:
            console.print(f"[red]episode_type must be 'TGL' or 'BONUS'[/red]")
            raise typer.Exit(1)

        value = value.upper()

        if value == old_value:
            console.print(f"[yellow]episode_type is already {value}[/yellow]")
            return

        console.print(f"\n[yellow]Changing episode_type from {old_value} to {value}[/yellow]")
        console.print("[dim]This will trigger ID recalculation...[/dim]\n")

        # Change the type
        episode.episode_type = value
        episode.manual_overrides.add('episode_type')

        # Recalculate IDs for all episodes
        _recalculate_episode_ids(episodes)

        # Save updated cache
        cache._save_cache(episodes)

        # Find the episode again (it may have a new ID)
        new_ep = None
        for ep in episodes:
            if ep.guid == episode.guid:
                new_ep = ep
                break

        if new_ep:
            console.print(f"[green]✓[/green] Episode type changed to {value}")
            console.print(f"[green]✓[/green] New episode ID: {new_ep.episode_id}")
        else:
            console.print(f"[red]Failed to find episode after recalculation[/red]")
            raise typer.Exit(1)

        return

    # Special handling for episode_id changes
    if field == 'episode_id':
        new_id = value.upper()

        # Validate format
        try:
            numeric_id = parse_episode_id(new_id)
        except ValueError as e:
            console.print(f"[red]Invalid episode ID format: {e}[/red]")
            raise typer.Exit(1)

        # Check for duplicates
        for ep in episodes:
            if ep.episode_id == new_id and ep.guid != episode.guid:
                console.print(f"[red]Episode ID {new_id} already exists[/red]")
                raise typer.Exit(1)

        console.print(f"\n[yellow]Changing episode_id from {episode.episode_id} to {new_id}[/yellow]\n")

        # Update episode_type based on new ID
        if new_id.startswith('E'):
            episode.episode_type = 'TGL'
            episode.id = numeric_id
        elif new_id.startswith('B'):
            episode.episode_type = 'BONUS'
            episode.id = numeric_id

        episode.episode_id = new_id
        episode.manual_overrides.add('episode_id')
        episode.manual_overrides.add('episode_type')  # Type is implicitly set too

        # Update full_title
        if episode.episode_type == 'TGL':
            episode.full_title = f"TGL {new_id}: {episode.title}"
        else:
            episode.full_title = f"BONUS {new_id}: {episode.title}"

        # Save
        episodes[episode_idx] = episode
        cache._save_cache(episodes)

        console.print(f"[green]✓[/green] Episode ID changed to {new_id}")
        console.print(f"[green]✓[/green] Episode type set to {episode.episode_type}")
        return

    # Handle other fields
    # Convert value to appropriate type
    field_type = episode.model_fields[field].annotation

    try:
        if field_type == int or 'int' in str(field_type):
            value = int(value)
        elif field_type == bool or 'bool' in str(field_type):
            value = value.lower() in ('true', '1', 'yes', 'y')
        # else keep as string
    except (ValueError, TypeError) as e:
        console.print(f"[red]Invalid value type: {e}[/red]")
        raise typer.Exit(1)

    # Set the value
    setattr(episode, field, value)
    episode.manual_overrides.add(field)

    # Save
    episodes[episode_idx] = episode
    cache._save_cache(episodes)

    console.print(f"\n[green]✓[/green] {field} changed from [dim]{old_value}[/dim] to [bold]{value}[/bold]")
    console.print(f"[dim]This field is now marked as manually overridden[/dim]\n")


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


def _get_audio_cache_path(audio_url: str) -> Path:
    """Get the cached audio file path for a given URL (for download command cache)

    Uses SHA1 hash of the URL (without query parameters) as filename.
    """
    # Parse URL and remove query parameters
    parsed = urlparse(audio_url)
    clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

    # Generate SHA1 hash of clean URL
    url_hash = hashlib.sha1(clean_url.encode('utf-8')).hexdigest()

    # Get file extension from path (default to .mp3)
    ext = Path(parsed.path).suffix or '.mp3'

    return paths.audio_cache_dir / f"{url_hash}{ext}"


@app.command()
def download(
    episode_ids: Optional[List[str]] = typer.Argument(None, help="Episode IDs to download (e.g., E390 E391 B01)"),
    tgl: bool = typer.Option(False, "--tgl", help="Download all TGL episodes"),
    bonus: bool = typer.Option(False, "--bonus", help="Download all BONUS episodes"),
    all: bool = typer.Option(False, "--all", help="Download all episodes"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing files")
):
    """Download episode audio files with verification and metadata extraction

    Features:
    - Files saved with correct extensions (.mp3, .wav, .m4a, .aac, .flac, etc.)
    - Verifies file sizes match RSS feed before skipping
    - Extracts duration metadata from audio files
    - Concurrent downloads (up to 5 at once)
    - Detailed error reporting with clickable Patreon links

    Examples:
      tgl download E390           # Download single episode
      tgl download E390 E391 B01  # Download multiple episodes
      tgl download --tgl          # Download all TGL episodes
      tgl download --bonus        # Download all BONUS episodes
      tgl download --all          # Download all episodes
      tgl download E390 --force   # Re-download even if exists
    """
    from .fetcher import PatreonPodcastFetcher, MUTAGEN_AVAILABLE

    cache = MetadataCache()

    # Auto-refresh if cache is stale or empty
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    # Determine which episodes to download
    episodes_to_download = []

    if all:
        episodes_to_download = cache.get_all_episodes()
    elif tgl:
        episodes_to_download = [ep for ep in cache.get_all_episodes() if ep.episode_type == 'TGL']
    elif bonus:
        episodes_to_download = [ep for ep in cache.get_all_episodes() if ep.episode_type == 'BONUS']
    elif episode_ids:
        # Parse individual episode IDs
        for ep_id in episode_ids:
            try:
                numeric_id = parse_episode_id(ep_id)
                episode = cache.get_episode(numeric_id)
                if episode:
                    episodes_to_download.append(episode)
                else:
                    console.print(f"[yellow]Warning: Episode {ep_id} not found[/yellow]")
            except ValueError as e:
                console.print(f"[yellow]Warning: Invalid episode ID {ep_id}[/yellow]")
    else:
        console.print("[red]Error: Please specify episode IDs or use --tgl, --bonus, or --all[/red]")
        raise typer.Exit(1)

    if not episodes_to_download:
        console.print("[yellow]No episodes to download[/yellow]")
        raise typer.Exit(0)

    # Filter out episodes without audio URLs
    episodes_with_audio = [ep for ep in episodes_to_download if ep.audio_url]
    if len(episodes_with_audio) < len(episodes_to_download):
        missing_count = len(episodes_to_download) - len(episodes_with_audio)
        console.print(f"[yellow]Warning: {missing_count} episode(s) have no audio URL[/yellow]")

    if not episodes_with_audio:
        console.print("[red]No episodes with audio URLs found[/red]")
        raise typer.Exit(1)

    # Create directory structure
    paths.audio_cache_dir.mkdir(parents=True, exist_ok=True)
    paths.tgl_episodes_dir.mkdir(parents=True, exist_ok=True)
    paths.bonus_episodes_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold cyan]Downloading {len(episodes_with_audio)} episode(s)[/bold cyan]")
    console.print(f"[dim]Using up to 5 concurrent downloads[/dim]")
    if MUTAGEN_AVAILABLE:
        console.print("[dim]Duration metadata will be extracted from downloaded files[/dim]\n")
    else:
        console.print("[dim]Install mutagen to extract duration metadata[/dim]\n")

    # Download episodes with progress tracking
    stats = {
        'downloaded': 0,
        'skipped': 0,
        'linked': 0,
        'failed': 0,
        'durations': 0
    }
    failed_episodes = []  # Track failed episodes with error details

    async def download_episode(client: httpx.AsyncClient, episode, progress, overall_task, semaphore):
        """Download a single episode asynchronously"""
        async with semaphore:  # Limit concurrent downloads
            # Determine destination directory
            if episode.episode_type == 'TGL':
                dest_dir = paths.tgl_episodes_dir
            else:
                dest_dir = paths.bonus_episodes_dir

            # Get cached file path to determine actual file extension
            cached_path = _get_audio_cache_path(episode.audio_url)
            file_extension = cached_path.suffix or '.mp3'

            # Build filename with correct extension
            filename = f"{episode.episode_id} - {episode.title}{file_extension}"
            # Clean filename of invalid characters
            filename = re.sub(r'[<>:"/\\|?*]', '', filename)
            dest_path = dest_dir / filename

            # Check if we should skip (destination exists and file is correct)
            should_skip = False
            if dest_path.exists() and not force:
                # Verify file size matches RSS feed if available
                if episode.audio_size:
                    actual_size = dest_path.stat().st_size
                    if actual_size != episode.audio_size:
                        # File size mismatch, need to re-download
                        progress.update(overall_task, description=f"[yellow]Size mismatch for {episode.episode_id}, re-downloading[/yellow]")
                        dest_path.unlink()
                        if cached_path.exists():
                            cached_path.unlink()
                    else:
                        should_skip = True
                else:
                    # No size info, assume file is correct
                    should_skip = True

            # If skipping, still check duration and extract if missing
            if should_skip:
                if not episode.duration and cached_path.exists() and MUTAGEN_AVAILABLE:
                    try:
                        from mutagen import File as MutagenFile
                        audio = MutagenFile(cached_path)
                        if audio is not None and audio.info and audio.info.length:
                            episode.duration = PatreonPodcastFetcher("")._format_duration(int(audio.info.length))
                            cache.add_episode(episode)
                            stats['durations'] += 1
                    except Exception:
                        pass

                progress.update(overall_task, advance=1, description=f"[yellow]Skipped {episode.episode_id}[/yellow]")
                stats['skipped'] += 1
                return

            # Download to cache if not already there (or if we need to re-download)
            needs_download = not cached_path.exists()
            if needs_download:
                try:
                    progress.update(overall_task, description=f"[cyan]Downloading {episode.episode_id}...[/cyan]")

                    async with client.stream('GET', episode.audio_url, timeout=60.0) as response:
                        response.raise_for_status()

                        with open(cached_path, 'wb') as f:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                f.write(chunk)

                    stats['downloaded'] += 1

                except httpx.HTTPStatusError as e:
                    # HTTP error with status code
                    error_msg = f"HTTP {e.response.status_code}"
                    failed_episodes.append({
                        'episode': episode,
                        'error': error_msg,
                        'url': episode.audio_url
                    })
                    progress.update(overall_task, description=f"[red]Failed {episode.episode_id}[/red]")
                    stats['failed'] += 1
                    if cached_path.exists():
                        cached_path.unlink()
                    progress.update(overall_task, advance=1)
                    return
                except httpx.TimeoutException as e:
                    # Timeout error
                    error_msg = "Timeout (60s)"
                    failed_episodes.append({
                        'episode': episode,
                        'error': error_msg,
                        'url': episode.audio_url
                    })
                    progress.update(overall_task, description=f"[red]Failed {episode.episode_id}[/red]")
                    stats['failed'] += 1
                    if cached_path.exists():
                        cached_path.unlink()
                    progress.update(overall_task, advance=1)
                    return
                except Exception as e:
                    # Other errors
                    error_msg = f"{type(e).__name__}: {str(e)}"
                    failed_episodes.append({
                        'episode': episode,
                        'error': error_msg,
                        'url': episode.audio_url
                    })
                    progress.update(overall_task, description=f"[red]Failed {episode.episode_id}[/red]")
                    stats['failed'] += 1
                    if cached_path.exists():
                        cached_path.unlink()
                    progress.update(overall_task, advance=1)
                    return

            # Create hard link from cache to destination
            try:
                progress.update(overall_task, description=f"[cyan]Linking {episode.episode_id}...[/cyan]")

                # Remove existing destination if forcing
                if dest_path.exists():
                    dest_path.unlink()

                # Create hard link
                os.link(cached_path, dest_path)

                if not needs_download:
                    stats['linked'] += 1

                # Extract duration from cached file if missing
                if not episode.duration and MUTAGEN_AVAILABLE:
                    try:
                        from mutagen import File as MutagenFile
                        audio = MutagenFile(cached_path)
                        if audio is not None and audio.info and audio.info.length:
                            # Update episode duration
                            episode.duration = PatreonPodcastFetcher("")._format_duration(int(audio.info.length))
                            # Update in cache
                            cache.add_episode(episode)
                            stats['durations'] += 1
                    except Exception:
                        pass  # Silently fail duration extraction

            except Exception as e:
                error_msg = f"Link failed: {type(e).__name__}: {str(e)}"
                failed_episodes.append({
                    'episode': episode,
                    'error': error_msg,
                    'url': episode.audio_url
                })
                progress.update(overall_task, description=f"[red]Failed to link {episode.episode_id}[/red]")
                stats['failed'] += 1

            progress.update(overall_task, advance=1)

    async def download_all():
        """Download all episodes concurrently"""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                overall_task = progress.add_task(
                    f"[cyan]Downloading episodes...",
                    total=len(episodes_with_audio)
                )

                # Semaphore to limit concurrent downloads (max 5)
                semaphore = asyncio.Semaphore(5)

                # Create tasks for all episodes
                tasks = [
                    download_episode(client, episode, progress, overall_task, semaphore)
                    for episode in episodes_with_audio
                ]

                # Run all downloads concurrently
                await asyncio.gather(*tasks)

    # Run the async download
    asyncio.run(download_all())

    # Save cache to persist durations
    if stats['durations'] > 0:
        cache.save()

    # Print summary
    console.print(f"\n[bold green]Download complete![/bold green]")
    console.print(f"  Downloaded: {stats['downloaded']}")
    if stats['linked'] > 0:
        console.print(f"  Linked from cache: {stats['linked']}")
    if stats['skipped'] > 0:
        console.print(f"  Skipped (already exists): {stats['skipped']}")
    if stats['failed'] > 0:
        console.print(f"  [red]Failed: {stats['failed']}[/red]")
    if stats['durations'] > 0:
        console.print(f"  [cyan]Durations extracted: {stats['durations']}[/cyan]")

    # Display failed episodes with details
    if failed_episodes:
        console.print(f"\n[bold red]Failed Downloads:[/bold red]")
        for failure in failed_episodes:
            ep = failure['episode']
            error = failure['error']
            url = failure['url']

            # Create clickable episode ID
            clickable_id = Text(ep.episode_id or "Unknown", style="bold")
            if ep.link:
                clickable_id.stylize(f"link {ep.link}")

            # Build the episode line with clickable ID
            episode_line = Text()
            episode_line.append("  ")
            episode_line.append("• ", style="red")
            episode_line.append(clickable_id)  # Don't apply style here, it's already styled
            episode_line.append(f" - {ep.title or 'Unknown'}")

            console.print(episode_line)
            console.print(f"    [dim]Error:[/dim] {error}")
            if url:
                console.print(f"    [dim]Audio URL:[/dim] [link={url}]{url[:80]}{'...' if len(url) > 80 else ''}[/link]")

    console.print(f"\n[dim]Files saved to: {paths.episodes_dir}[/dim]")


def _transcribe_no_ui(episodes: List, transcription_cache, cache, model_size: str = "large-v3", batch_size: Optional[int] = None):
    """Transcribe episodes without TUI (for debugging)"""
    import httpx

    console.print(f"\n[bold cyan]{'═' * 60}[/bold cyan]")
    console.print(f"[bold cyan]Transcribing {len(episodes)} Episode(s) (No UI Mode)[/bold cyan]")
    console.print(f"[bold cyan]{'═' * 60}[/bold cyan]\n")

    transcription_count = 0
    error_count = 0
    errors = []

    for idx, ep in enumerate(episodes, 1):
        console.print(f"\n[cyan]━━━ [{idx}/{len(episodes)}] {ep.episode_id}: {ep.title} ━━━[/cyan]\n")

        guid = ep.guid or str(ep.id)
        audio_path = _get_cached_audio_path(ep)

        # Download if needed
        if not audio_path:
            console.print(f"[yellow]Downloading episode...[/yellow]")
            try:
                dest_path = _get_episode_audio_path(ep, check_exists=False)
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                if not ep.audio_url:
                    raise RuntimeError("No audio URL available")

                with httpx.stream("GET", ep.audio_url, follow_redirects=True, timeout=300.0) as response:
                    response.raise_for_status()
                    total_size = int(response.headers.get('content-length', 0))

                    with open(dest_path, 'wb') as f:
                        downloaded = 0
                        for chunk in response.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                pct = (downloaded / total_size) * 100
                                console.print(f"\r[cyan]Downloaded: {pct:.1f}% ({downloaded / 1024 / 1024:.1f}MB / {total_size / 1024 / 1024:.1f}MB)[/cyan]", end="")
                    console.print()  # New line after progress

                audio_path = dest_path
                console.print(f"[green]✓ Downloaded[/green]")

            except Exception as e:
                console.print(f"\n[red]✗ Download failed: {e}[/red]")
                errors.append((ep, f"Download failed: {e}"))
                error_count += 1
                continue

        # Transcribe
        console.print(f"[yellow]Transcribing (this may take a while)...[/yellow]")
        try:
            # Simple callback to show progress
            import sys
            last_pct = [0]
            def on_progress(pct):
                if int(pct) > last_pct[0]:
                    last_pct[0] = int(pct)
                    # Use plain print with \r for carriage return (Rich doesn't handle this well)
                    print(f"\rTranscription progress: {pct:.1f}%", end="", flush=True)

            transcription_text, segments = transcribe_audio(audio_path, model_size=model_size, progress_callback=on_progress, batch_size=batch_size)
            print()  # New line after progress

            # Save transcription with timestamps
            transcription_cache.add_transcription(guid, transcription_text, segments)
            transcription_cache.save()

            console.print(f"[green]✓ Transcribed ({len(transcription_text)} characters, {len(segments)} segments)[/green]")
            transcription_count += 1

        except Exception as e:
            console.print(f"\n[red]✗ Transcription failed: {e}[/red]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
            errors.append((ep, f"Transcription failed: {e}"))
            error_count += 1

    # Summary
    console.print(f"\n[bold cyan]{'═' * 60}[/bold cyan]")
    console.print(f"[bold cyan]Transcription Summary[/bold cyan]")
    console.print(f"[bold cyan]{'═' * 60}[/bold cyan]\n")

    console.print(f"[green]✓ Successfully transcribed {transcription_count} episode(s)[/green]")

    if errors:
        console.print(f"\n[red]✗ Failed {error_count} episode(s):[/red]")
        for ep, error in errors:
            console.print(f"  • [bold]{ep.episode_id}[/bold]: {ep.title[:50]}")
            console.print(f"    [dim]{error}[/dim]")
        console.print()

    # Rebuild search index to include new transcriptions
    if transcription_count > 0:
        console.print("[cyan]Updating search index...[/cyan]")
        search_index = SearchIndex()
        search_index.build_index(cache.episodes)
        console.print("[green]✓[/green] Search index updated\n")


@app.command()
def transcribe(
    episode_ids: Optional[List[str]] = typer.Argument(None, help="Episode IDs or ranges (e.g., E390, E100-E150)"),
    all_episodes: bool = typer.Option(False, "--all", help="Transcribe all episodes"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-transcribe even if transcription exists"),
    no_ui: bool = typer.Option(False, "--no-ui", help="Disable TUI, show progress bars instead (for debugging)"),
    model: str = typer.Option("large-v3", "--model", "-m", help="Whisper model (turbo, large-v3, large-v2, medium, small, base, tiny)"),
    batch_size: Optional[int] = typer.Option(None, "--batch-size", "-b", help="Batch size for faster processing (recommended: 8-24)")
):
    """Transcribe episode audio files using Whisper AI

    Downloads episodes if needed, then transcribes them using faster-whisper.
    Transcriptions are automatically integrated into the search index.

    Shows a live TUI with:
    - Overall progress statistics
    - Episode list with status icons
    - Live transcription text scrolling
    - Download progress for each episode

    Examples:
      tgl transcribe E390              # Transcribe single episode
      tgl transcribe E390 E391 B01     # Transcribe multiple episodes
      tgl transcribe E100-E150         # Transcribe episode range (inclusive)
      tgl transcribe E100-E110 E200    # Mix ranges and individual episodes
      tgl transcribe --all             # Transcribe all episodes
      tgl transcribe E390 --force      # Re-transcribe even if exists
      tgl transcribe E390 --no-ui      # Debug mode without TUI
      tgl transcribe E390 -m turbo     # Use faster turbo model
      tgl transcribe E390 -m medium    # Use smaller, faster medium model
      tgl transcribe E390 -b 16        # Use batched processing for speed
    """
    import concurrent.futures
    from queue import Queue
    import threading
    from tgl.transcribe_ui import TranscriptionApp, EpisodeState

    cache = MetadataCache()
    transcription_cache = TranscriptionCache()

    # Auto-refresh if cache is stale or empty
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    # Determine which episodes to transcribe
    episodes_to_process = []

    if all_episodes:
        episodes_to_process = cache.get_all_episodes()
    elif episode_ids:
        # Expand any ranges first (e.g., E100-E150)
        expanded_ids = []
        for ep_id_str in episode_ids:
            range_ids = parse_episode_range(ep_id_str, cache.get_all_episodes())
            expanded_ids.extend(range_ids)

        # Show expanded range info
        if len(expanded_ids) > len(episode_ids):
            console.print(f"[dim]Expanded to {len(expanded_ids)} episode(s)[/dim]")

        # Find episodes for all IDs (including expanded ranges)
        for ep_id_str in expanded_ids:
            episode, _ = find_episode_by_id_or_guid(cache.get_all_episodes(), ep_id_str)
            if episode:
                episodes_to_process.append(episode)
            else:
                console.print(f"[yellow]Warning: Episode {ep_id_str} not found, skipping[/yellow]")
    else:
        console.print("[red]Error: Provide episode IDs or use --all flag[/red]")
        raise typer.Exit(1)

    if not episodes_to_process:
        console.print("[yellow]No episodes to transcribe[/yellow]")
        return

    # Filter episodes that need transcription
    if not force:
        episodes_needing_transcription = [
            ep for ep in episodes_to_process
            if not transcription_cache.has_transcription(ep.guid or str(ep.id))
        ]
        skipped_count = len(episodes_to_process) - len(episodes_needing_transcription)
        if skipped_count > 0:
            console.print(f"[dim]Skipping {skipped_count} episode(s) with existing transcriptions[/dim]")
        episodes_to_process = episodes_needing_transcription

    if not episodes_to_process:
        console.print("[green]All episodes already transcribed[/green]")
        return

    # Use simple progress bars if --no-ui flag is set
    if no_ui:
        _transcribe_no_ui(episodes_to_process, transcription_cache, cache, model_size=model, batch_size=batch_size)
        return

    # Queues for coordinating work
    # ARCHITECTURE:
    # - Downloads happen in worker threads (3 concurrent)
    # - Downloaded episodes go into transcribe_queue
    # - DEDICATED transcription worker thread owns PyTorch model and does transcription
    # - Transcription worker sends messages to results_queue (only simple Python objects!)
    # - TUI reads messages from results_queue and updates UI
    # - NO PyTorch tensors ever cross thread boundaries!
    download_queue = Queue()
    transcribe_queue = Queue()
    results_queue = Queue()  # For messages from transcription worker to TUI
    shutdown_event = threading.Event()  # Signal workers to stop

    # Store thread references for graceful shutdown
    worker_threads = []

    # Fill download queue
    for ep in episodes_to_process:
        download_queue.put(ep)

    # Worker function for downloads (runs in worker threads)
    def download_worker(app: TranscriptionApp):
        """Download episodes and queue them for transcription"""
        import httpx

        while not shutdown_event.is_set():
            try:
                ep = download_queue.get_nowait()
            except:
                # Queue empty, exit normally
                break

            # Check for shutdown before processing
            if shutdown_event.is_set():
                download_queue.task_done()
                break

            guid = ep.guid or str(ep.id)

            # Check if already exists in episodes dir
            if _get_cached_audio_path(ep):
                # Already downloaded, skip to transcription
                transcribe_queue.put(ep)
                download_queue.task_done()
                continue

            # Get destination path (create parent dirs)
            dest_path = _get_episode_audio_path(ep, check_exists=False)
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # Check if file exists in audio cache (from download command)
            if ep.audio_url:
                cached_audio = _get_audio_cache_path(ep.audio_url)
                if cached_audio.exists():
                    # File exists in cache, create hard link instead of re-downloading
                    try:
                        import os
                        if dest_path.exists():
                            dest_path.unlink()
                        os.link(cached_audio, dest_path)

                        # Mark as downloaded (from cache)
                        app.call_from_thread(
                            app.update_episode_state,
                            guid,
                            EpisodeState.DOWNLOADED
                        )
                        app.call_from_thread(app.clear_download)

                        # Queue for transcription
                        transcribe_queue.put(ep)
                        download_queue.task_done()
                        continue
                    except Exception as e:
                        # Hard link failed, fall through to download
                        console.print(f"[yellow]Warning: Could not link from cache for {ep.episode_id}: {e}[/yellow]")
                        if dest_path.exists():
                            dest_path.unlink()

            # Update UI: start downloading
            app.call_from_thread(
                app.update_episode_state,
                guid,
                EpisodeState.DOWNLOADING,
                download_progress=0.0
            )
            app.call_from_thread(
                app.update_download_progress,
                ep.episode_id or "Unknown",
                0.0
            )

            try:
                if not ep.audio_url:
                    raise RuntimeError("No audio URL available")

                # Download with progress tracking
                with httpx.stream("GET", ep.audio_url, follow_redirects=True, timeout=300.0) as response:
                    response.raise_for_status()
                    total_size = int(response.headers.get('content-length', 0))

                    with open(dest_path, 'wb') as f:
                        downloaded = 0
                        for chunk in response.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                            downloaded += len(chunk)

                            if total_size > 0:
                                progress_pct = (downloaded / total_size) * 100
                                app.call_from_thread(
                                    app.update_episode_state,
                                    guid,
                                    EpisodeState.DOWNLOADING,
                                    download_progress=progress_pct
                                )
                                app.call_from_thread(
                                    app.update_download_progress,
                                    ep.episode_id or "Unknown",
                                    progress_pct,
                                    f"{downloaded / 1024 / 1024:.1f}MB / {total_size / 1024 / 1024:.1f}MB"
                                )

                # Mark as downloaded
                app.call_from_thread(
                    app.update_episode_state,
                    guid,
                    EpisodeState.DOWNLOADED
                )
                app.call_from_thread(app.clear_download)

                # Queue for transcription
                transcribe_queue.put(ep)

            except Exception as e:
                # Mark as error and log it
                import traceback
                error_msg = f"{type(e).__name__}: {str(e)}"
                app.call_from_thread(
                    app.update_episode_state,
                    guid,
                    EpisodeState.ERROR,
                    error_message=error_msg
                )
                app.call_from_thread(app.clear_download)
                # Log full traceback for debugging
                console.print(f"\n[red]Download error for {ep.episode_id}:[/red]")
                console.print(f"[dim]{traceback.format_exc()}[/dim]")

            download_queue.task_done()

    # Dedicated transcription worker (runs in its own thread, owns PyTorch model)
    def transcription_worker():
        """Transcribe episodes - THIS THREAD OWNS ALL PyTorch OPERATIONS!"""
        from tgl.transcribe_ui import TranscriptionMessage

        # This thread will keep running until all episodes are processed or shutdown
        while not shutdown_event.is_set():
            try:
                ep = transcribe_queue.get(timeout=1.0)
            except:
                # Check if downloads are done or shutdown requested
                if download_queue.unfinished_tasks == 0 or shutdown_event.is_set():
                    break
                continue

            # Check for shutdown before processing
            if shutdown_event.is_set():
                transcribe_queue.task_done()
                break

            guid = ep.guid or str(ep.id)
            audio_path = _get_cached_audio_path(ep)

            if not audio_path or not audio_path.exists():
                # Send error message (only simple Python objects!)
                results_queue.put(TranscriptionMessage.error(guid, "Audio file not found"))
                transcribe_queue.task_done()
                continue

            # Send message that we're starting (only simple Python objects!)
            results_queue.put({"type": "start", "guid": guid})

            # Callbacks to send messages to TUI (only simple Python objects!)
            def on_segment(text: str):
                results_queue.put(TranscriptionMessage.segment(guid, text))

            def on_progress(pct: float):
                results_queue.put(TranscriptionMessage.progress(guid, pct))

            def on_vad_complete():
                results_queue.put(TranscriptionMessage.vad_complete(guid))

            def on_shutdown_check() -> bool:
                return shutdown_event.is_set()

            try:
                # Transcribe - THIS IS THE ONLY PLACE PyTorch IS USED!
                # All PyTorch tensors stay in this thread!
                transcription_text, segments = transcribe_audio(
                    audio_path,
                    model_size=model,
                    segment_callback=on_segment,
                    progress_callback=on_progress,
                    shutdown_callback=on_shutdown_check,
                    batch_size=batch_size,
                    vad_complete_callback=on_vad_complete
                )

                # Send completion message with segments (only simple Python objects!)
                results_queue.put(TranscriptionMessage.complete(guid, transcription_text, segments))

            except Exception as e:
                # Check if this was a shutdown abort
                if "aborted due to shutdown" in str(e):
                    # Don't send error message for intentional shutdown
                    transcribe_queue.task_done()
                    break

                # Send error message (only simple Python objects!)
                import traceback
                error_msg = f"{type(e).__name__}: {str(e)}"
                results_queue.put(TranscriptionMessage.error(guid, error_msg))
                console.print(f"\n[red]Transcription error for {ep.episode_id}:[/red]")
                console.print(f"[dim]{traceback.format_exc()}[/dim]")

            transcribe_queue.task_done()

    # Start workers callback
    def start_workers(app: TranscriptionApp):
        """Start download and transcription workers"""
        # Start download workers (3 concurrent downloads)
        for i in range(3):
            t = threading.Thread(target=download_worker, args=(app,), daemon=False, name=f"Download-{i+1}")
            t.start()
            worker_threads.append(t)

        # Start dedicated transcription worker (owns PyTorch model!)
        transcribe_thread = threading.Thread(target=transcription_worker, daemon=False, name="Transcription")
        transcribe_thread.start()
        worker_threads.append(transcribe_thread)

    # Create and run the TUI
    # ARCHITECTURE:
    # - TUI runs in Textual's event loop
    # - Download workers run in 3 threads
    # - Transcription worker runs in 1 dedicated thread (owns PyTorch!)
    # - Communication via results_queue (only simple Python objects!)
    app_instance = TranscriptionApp(
        episodes=episodes_to_process,
        transcription_cache=transcription_cache,
        download_callback=start_workers,
        results_queue=results_queue
    )
    app_instance.run(mouse=False)

    # TUI has exited - signal workers to shut down gracefully
    console.print("[dim]Shutting down workers...[/dim]")
    shutdown_event.set()

    # Wait for all worker threads to finish (with timeout)
    # Short timeout since transcription aborts immediately on shutdown
    for thread in worker_threads:
        thread.join(timeout=3.0)
        if thread.is_alive():
            console.print(f"[yellow]Warning: {thread.name} thread did not finish within 3 seconds[/yellow]")

    # Print summary
    from tgl.transcribe_ui import EpisodeState
    statuses = app_instance.episode_statuses
    transcribed_episodes = [s for s in statuses.values() if s.state == EpisodeState.TRANSCRIBED]
    failed_episodes = [s for s in statuses.values() if s.state == EpisodeState.ERROR]

    console.print(f"\n[bold cyan]{'═' * 60}[/bold cyan]")
    console.print(f"[bold cyan]Transcription Summary[/bold cyan]")
    console.print(f"[bold cyan]{'═' * 60}[/bold cyan]\n")

    if transcribed_episodes:
        console.print(f"[green]✓ Successfully transcribed {len(transcribed_episodes)} episode(s):[/green]")
        for status in transcribed_episodes:
            console.print(f"  • {status.episode.episode_id}: {status.episode.title[:50]}")
        console.print()

    if failed_episodes:
        console.print(f"[red]✗ Failed {len(failed_episodes)} episode(s):[/red]")
        for status in failed_episodes:
            console.print(f"  • [bold]{status.episode.episode_id}[/bold]: {status.episode.title[:50]}")
            if status.error_message:
                console.print(f"    [dim]Error: {status.error_message}[/dim]")
        console.print()

    # Rebuild search index to include new transcriptions
    if transcribed_episodes:
        console.print("[cyan]Updating search index...[/cyan]")
        search_index = SearchIndex()
        search_index.build_index(cache.episodes)
        console.print("[green]✓[/green] Search index updated\n")


def _get_episode_audio_path(episode, check_exists: bool = True) -> Optional[Path]:
    """Get the path to an episode's audio file

    Args:
        episode: Episode object
        check_exists: If True, return None if file doesn't exist

    Returns:
        Path to audio file, or None if check_exists=True and file doesn't exist
    """
    if episode.episode_type == 'TGL':
        episodes_subdir = paths.episodes_dir / "tgl"
    else:
        episodes_subdir = paths.episodes_dir / "bonus"

    # Extract extension from audio URL
    ext = '.mp3'  # Default
    if episode.audio_url:
        parsed = urlparse(episode.audio_url)
        path_parts = Path(parsed.path).parts
        for part in reversed(path_parts):
            if '.' in part:
                found_ext = Path(part).suffix
                if found_ext:
                    ext = found_ext
                    break

    audio_path = episodes_subdir / f"{episode.episode_id}{ext}"

    if check_exists:
        return audio_path if audio_path.exists() else None
    return audio_path


def _get_cached_audio_path(episode) -> Optional[Path]:
    """Get the path to a cached audio file for an episode (returns None if doesn't exist)"""
    return _get_episode_audio_path(episode, check_exists=True)


def _download_episode(episode, force=False):
    """Download a single episode (helper for transcribe command)"""
    import httpx

    if episode.episode_type == 'TGL':
        episodes_subdir = paths.episodes_dir / "tgl"
    else:
        episodes_subdir = paths.episodes_dir / "bonus"

    episodes_subdir.mkdir(parents=True, exist_ok=True)

    # Extract extension from URL
    if episode.audio_url:
        parsed = urlparse(episode.audio_url)
        path_parts = Path(parsed.path).parts
        for part in reversed(path_parts):
            if '.' in part:
                ext = Path(part).suffix
                if ext:
                    break
        else:
            ext = '.mp3'
    else:
        ext = '.mp3'

    dest_path = episodes_subdir / f"{episode.episode_id}{ext}"

    # Skip if exists and not forcing
    if dest_path.exists() and not force:
        return dest_path

    if not episode.audio_url:
        raise RuntimeError("No audio URL available")

    # Download the file
    with httpx.stream("GET", episode.audio_url, follow_redirects=True, timeout=300.0) as response:
        response.raise_for_status()
        with open(dest_path, 'wb') as f:
            for chunk in response.iter_bytes(chunk_size=8192):
                f.write(chunk)

    return dest_path


@app.command()
def spotify(
    identifiers: Optional[List[str]] = typer.Argument(None, help="Years (4 digits) or episode IDs (e.g., 2024, 390, E390, B01)"),
    all_years: bool = typer.Option(False, "--years", help="Create playlists for all years with episodes"),
    all_tracks: bool = typer.Option(False, "--all", help="Create playlist with ALL tracks from all episodes"),
    sync: bool = typer.Option(False, "--sync", help="Update all playlists currently tracked in state"),
    dry_run: bool = typer.Option(False, "-n", "--dry-run", help="Dry run mode (no write operations)"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show all Spotify API calls"),
    search_missing: bool = typer.Option(False, "-U", "--search-missing", help="Re-search missing tracks (ignore cache for tracks not found)"),
):
    """Manage Spotify playlists for TGL episodes

    Run without arguments to authorize Spotify access.

    Provide years (4 digits) or episode IDs as arguments:

      tgl spotify 2024           # Year 2024
      tgl spotify 390            # Episode E390 (auto-detected as TGL)
      tgl spotify E390           # Episode E390 (explicit)
      tgl spotify B01            # BONUS episode B01
      tgl spotify 2024 390 391   # Multiple years/episodes
      tgl spotify --all          # All tracks from all episodes
      tgl spotify --years        # Create playlists for all years
      tgl spotify --sync         # Update all tracked playlists
    """
    # Check if Spotify credentials are configured
    if not settings.spotify_client_id or not settings.spotify_client_secret:
        console.print("\n[red]✗ Spotify credentials not configured[/red]\n")
        console.print("[dim]To use Spotify integration, you need to:[/dim]\n")
        console.print("1. Create a Spotify app at: [cyan]https://developer.spotify.com/dashboard[/cyan]")
        console.print("2. Configure credentials with: [cyan]tgl config set spotify_client_id YOUR_ID[/cyan]")
        console.print("3. And: [cyan]tgl config set spotify_client_secret YOUR_SECRET[/cyan]\n")
        console.print("[dim]Or run: [cyan]tgl config init[/cyan] to reconfigure everything[/dim]\n")
        raise typer.Exit(1)

    # Parse identifiers into years and episodes
    years = []
    episodes = []

    if identifiers:
        for identifier in identifiers:
            # Check if it's a 4-digit year
            if identifier.isdigit() and len(identifier) == 4:
                years.append(int(identifier))
            # Check if it's a number with less than 4 digits (assume TGL episode)
            elif identifier.isdigit() and len(identifier) < 4:
                episodes.append(f"E{identifier}")
            # Otherwise treat as episode ID (E390, B01, etc.)
            else:
                episodes.append(identifier)

    # Validate that --years is mutually exclusive with year arguments
    if all_years and years:
        console.print("\n[red]Error: --years cannot be used with year arguments[/red]\n")
        console.print("[dim]Use --years to create playlists for all years, or specify individual years as arguments[/dim]\n")
        raise typer.Exit(1)

    # Validate that --sync is mutually exclusive with other playlist options
    if sync and (episodes or years or all_years or all_tracks):
        console.print("\n[red]Error: --sync cannot be used with episode/year arguments or --years/--all options[/red]\n")
        console.print("[dim]Use --sync alone to update all tracked playlists, or specify individual playlists without --sync[/dim]\n")
        raise typer.Exit(1)

    # Initialize Spotify manager
    from .spotify import SpotifyManager
    spotify_manager = SpotifyManager(settings, dry_run=dry_run, verbose=verbose, force_search_missing=search_missing)

    # If no arguments/options provided, just run authorization
    if not episodes and not years and not all_years and not all_tracks and not sync:
        if spotify_manager.authorize():
            console.print("[green]✓ Spotify authorization successful[/green]")
            console.print("[dim]You can now use Spotify commands like:[/dim]")
            console.print("[dim]  [cyan]tgl spotify 2024[/cyan] (year)[/dim]")
            console.print("[dim]  [cyan]tgl spotify 390[/cyan] (episode E390)[/dim]")
            console.print("[dim]  [cyan]tgl spotify E390 B01[/cyan] (multiple episodes)[/dim]")
            console.print("[dim]  [cyan]tgl spotify 2024 390[/cyan] (year + episode)[/dim]")
            console.print("[dim]  [cyan]tgl spotify --all[/cyan] (all tracks)[/dim]")
            console.print("[dim]  [cyan]tgl spotify --years[/cyan] (all years)[/dim]")
            console.print("[dim]  [cyan]tgl spotify --sync[/cyan] (update all tracked playlists)[/dim]\n")
        raise typer.Exit(0)

    # Load episode cache
    cache = MetadataCache()
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    # Get all episodes as a list
    all_episodes_list = cache.get_all_episodes()

    # Track overall success
    all_success = True

    # Handle --years: expand to all available years
    if all_years:
        available_years = cache.get_available_years()
        if not available_years:
            console.print("\n[yellow]No years with episodes found[/yellow]\n")
            raise typer.Exit(0)

        console.print(f"\n[bold cyan]Creating playlists for {len(available_years)} year(s): {', '.join(map(str, sorted(available_years)))}[/bold cyan]\n")
        years = available_years

    # Handle --sync: update all playlists in state
    if sync:
        playlists = spotify_manager.state.playlists

        if not playlists:
            console.print("\n[yellow]No playlists tracked in state yet[/yellow]")
            console.print("[dim]Create playlists first with --episode, --year, or --all[/dim]\n")
            raise typer.Exit(0)

        console.print(f"\n[bold cyan]Syncing {len(playlists)} tracked playlist(s)[/bold cyan]\n")

        for playlist_key in playlists.keys():
            # Parse playlist key to determine type
            if playlist_key.startswith("episode:"):
                episode_id_str = playlist_key.split(":", 1)[1]
                try:
                    episode_id = parse_episode_id(episode_id_str)
                    ep = cache.get_episode(episode_id)
                    if not ep:
                        console.print(f"[red]Error: Episode {episode_id_str} not found in cache (skipping)[/red]")
                        all_success = False
                        continue

                    success = spotify_manager.sync_episode_playlist(
                        ep,
                        playlist_format=settings.spotify_episode_playlist_format,
                        playlist_description=settings.spotify_episode_playlist_description
                    )
                    if not success:
                        all_success = False
                except ValueError as e:
                    console.print(f"[red]Error parsing episode ID {episode_id_str}: {e} (skipping)[/red]")
                    all_success = False

            elif playlist_key.startswith("year:"):
                year_str = playlist_key.split(":", 1)[1]
                try:
                    year = int(year_str)
                    success = spotify_manager.sync_year_playlist(
                        year,
                        all_episodes_list,
                        playlist_format=settings.spotify_year_playlist_format,
                        playlist_description=settings.spotify_year_playlist_description
                    )
                    if not success:
                        all_success = False
                except ValueError as e:
                    console.print(f"[red]Error parsing year {year_str}: {e} (skipping)[/red]")
                    all_success = False

            elif playlist_key == "all":
                success = spotify_manager.sync_all_playlist(
                    all_episodes_list,
                    playlist_format=settings.spotify_all_playlist_format,
                    playlist_description=settings.spotify_all_playlist_description
                )
                if not success:
                    all_success = False

            else:
                console.print(f"[yellow]Warning: Unknown playlist key '{playlist_key}' (skipping)[/yellow]")

        console.print(f"\n[bold cyan]Sync complete[/bold cyan]\n")
        if not all_success:
            raise typer.Exit(1)
        raise typer.Exit(0)

    # Process episode playlists
    if episodes:
        for episode_str in episodes:
            try:
                episode_id = parse_episode_id(episode_str)
                ep = cache.get_episode(episode_id)
                if not ep:
                    console.print(f"[red]Error: Episode {episode_str} not found[/red]")
                    all_success = False
                    continue

                success = spotify_manager.sync_episode_playlist(
                    ep,
                    playlist_format=settings.spotify_episode_playlist_format,
                    playlist_description=settings.spotify_episode_playlist_description
                )
                if not success:
                    all_success = False
            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                all_success = False

    # Process year playlists
    if years:
        for year in years:
            success = spotify_manager.sync_year_playlist(
                year,
                all_episodes_list,
                playlist_format=settings.spotify_year_playlist_format,
                playlist_description=settings.spotify_year_playlist_description
            )
            if not success:
                all_success = False

    # Process all-tracks playlist
    if all_tracks:
        success = spotify_manager.sync_all_playlist(
            all_episodes_list,
            playlist_format=settings.spotify_all_playlist_format,
            playlist_description=settings.spotify_all_playlist_description
        )
        if not success:
            all_success = False

    if not all_success:
        raise typer.Exit(1)


def find_episode_gaps(cached_episodes, console_obj):
    """Helper function to find gaps in TGL episode numbering"""
    from datetime import datetime

    # Get all TGL episodes sorted by episode number
    tgl_episodes = [ep for ep in cached_episodes if ep.episode_type == 'TGL' and ep.id > 0]
    tgl_episodes.sort(key=lambda ep: ep.id)

    gaps = []
    for i in range(len(tgl_episodes) - 1):
        current = tgl_episodes[i]
        next_ep = tgl_episodes[i + 1]

        # Check for gap (difference > 1)
        if next_ep.id - current.id > 1:
            # Find any episodes published between these two
            current_date = datetime.fromisoformat(current.published)
            next_date = datetime.fromisoformat(next_ep.published)

            # Find BONUS and other episodes in between
            in_between = []
            for ep in cached_episodes:
                try:
                    ep_date = datetime.fromisoformat(ep.published)
                    if current_date < ep_date < next_date and ep.episode_type != 'TGL':
                        in_between.append(ep)
                except (ValueError, AttributeError):
                    continue

            # Sort by published date
            in_between.sort(key=lambda ep: ep.published)

            gaps.append({
                'before': current,
                'after': next_ep,
                'missing_numbers': [*range(current.id + 1, next_ep.id)],  # Use list literal instead of list()
                'in_between': in_between
            })

    return gaps


@app.command()
def doctor(
    section: Optional[str] = typer.Argument(None, help="Section to show: 'missing', 'gaps', 'spotify', 'titles', or 'all' (default: all)")
):
    """Diagnose issues with episode metadata and Spotify track mappings

    This command helps identify:
    - Episodes available in RSS feed but missing from metadata cache
    - Gaps in TGL episode numbering
    - Tracks in metadata that couldn't be found on Spotify
    - Episode title processing (full vs cleaned titles)
    """
    import json

    # Normalize section argument
    valid_sections = {'missing', 'gaps', 'spotify', 'titles', 'all'}
    if section:
        section = section.lower()
        if section not in valid_sections:
            console.print(f"[red]Error: Invalid section '{section}'. Must be one of: {', '.join(sorted(valid_sections))}[/red]")
            raise typer.Exit(1)
    else:
        section = 'all'

    show_missing = section in ('missing', 'all')
    show_gaps = section in ('gaps', 'all')
    show_spotify = section in ('spotify', 'all')
    show_titles = section == 'titles'  # Only show when explicitly requested

    console.print("\n[bold cyan]" + "═" * 70)
    console.print("[bold cyan]TGL Doctor - Diagnostics Report")
    console.print("[bold cyan]" + "═" * 70 + "\n")

    # Load metadata cache
    cache = MetadataCache()

    # Fetch current episodes from RSS (only if needed)
    rss_episodes = []
    if show_missing or show_gaps:
        console.print("[cyan]Fetching episodes from RSS feed...[/cyan]")
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        rss_episodes = fetcher.fetch_episodes()
        console.print(f"[green]✓[/green] Found {len(rss_episodes)} episodes in RSS feed\n")

    # Section 1: Check for missing episodes
    cached_episodes = cache.get_all_episodes()

    if show_missing:
        console.print("[bold]1. Episodes in RSS but not in metadata cache:[/bold]\n")

        cached_links = {ep.link for ep in cached_episodes}
        missing_episodes = [ep for ep in rss_episodes if ep.link not in cached_links]

        if missing_episodes:
            console.print(f"[yellow]Found {len(missing_episodes)} missing episode(s):[/yellow]\n")
            for ep in missing_episodes:
                from rich.text import Text
                episode_id_link = Text(ep.episode_id)
                episode_id_link.stylize(f"link {ep.link}")
                console.print("  • ", episode_id_link, f" - {ep.title}", sep="")
                duration_info = f" | Duration: {ep.duration}" if ep.duration else ""
                console.print(f"    [dim]Published: {ep.published}{duration_info}[/dim]\n")
            console.print("[dim]Run 'tgl fetch' to update the metadata cache[/dim]\n")
        else:
            console.print("[green]✓ All RSS episodes are present in metadata cache[/green]\n")

    # Section 2: Check for gaps in TGL episode numbering
    if show_gaps:
        console.print("[bold]2. Gaps in TGL episode numbering:[/bold]\n")

        gaps = find_episode_gaps(cached_episodes, console)

        if gaps:
            console.print(f"[yellow]Found {len(gaps)} gap(s) in TGL episode numbering:[/yellow]\n")

            for gap in gaps:
                from rich.text import Text
                before = gap['before']
                after = gap['after']
                missing = gap['missing_numbers']
                in_between = gap['in_between']

                if len(missing) == 1:
                    missing_str = f"E{missing[0]}"
                elif len(missing) <= 3:
                    missing_str = ", ".join([f"E{n}" for n in missing])
                else:
                    missing_str = f"E{missing[0]}-E{missing[-1]} ({len(missing)} episodes)"

                console.print(f"[bold cyan]Missing: {missing_str}[/bold cyan]")

                # Before episode with clickable link
                before_link = Text(before.episode_id)
                before_link.stylize(f"link {before.link}")
                console.print("[dim]  Before:[/dim] ", before_link, f" - {before.title}", sep="")
                before_duration = f" | Duration: {before.duration}" if before.duration else ""
                console.print(f"[dim]         Published: {before.published}{before_duration}[/dim]")

                # After episode with clickable link
                after_link = Text(after.episode_id)
                after_link.stylize(f"link {after.link}")
                console.print("[dim]  After:[/dim]  ", after_link, f" - {after.title}", sep="")
                after_duration = f" | Duration: {after.duration}" if after.duration else ""
                console.print(f"[dim]         Published: {after.published}{after_duration}[/dim]")

                if in_between:
                    console.print(f"\n  [yellow]Published in between ({len(in_between)} episode(s)):[/yellow]")
                    for ep in in_between:
                        ep_link = Text(ep.episode_id)
                        ep_link.stylize(f"link {ep.link}")
                        console.print("    • ", ep_link, f" ({ep.episode_type}) - {ep.title}", sep="")
                        ep_duration = f" | Duration: {ep.duration}" if ep.duration else ""
                        console.print(f"      [dim]Published: {ep.published}{ep_duration}[/dim]")
                else:
                    console.print(f"\n  [dim]No episodes published in between[/dim]")

                console.print()
        else:
            console.print("[green]✓ No gaps found in TGL episode numbering[/green]\n")

    # Section 3: Check for tracks without Spotify IDs
    if show_spotify:
        console.print("[bold]3. Tracks without Spotify IDs:[/bold]\n")

        # Load Spotify state
        spotify_state_file = paths.data_dir / "spotify.json"
        if not spotify_state_file.exists():
            console.print("[yellow]No Spotify state file found (spotify.json)[/yellow]")
            console.print("[dim]Run 'tgl spotify --year 2024' or similar to search for tracks[/dim]\n")
        else:
            with open(spotify_state_file, 'r') as f:
                spotify_state = json.load(f)

            track_cache = spotify_state.get('tracks', {})

            # Build reverse lookup: artist|title -> spotify_id
            def make_key(artist: str, title: str) -> str:
                return f"{artist.lower()}|{title.lower()}"

            # Collect all tracks without Spotify IDs, grouped by episode
            episodes_with_missing = []

            for episode in sorted(cache.get_all_episodes(), key=lambda e: e.id):
                if not episode.tracklist:
                    continue

                missing_tracks = []
                for track in episode.tracklist:
                    # Check all possible keys (with and without variant)
                    keys_to_check = [make_key(track.artist, track.title)]
                    if track.variant:
                        keys_to_check.append(make_key(track.artist, f"{track.title} {track.variant}"))

                    # Check if any key has a successful match
                    found = False
                    for key in keys_to_check:
                        if key in track_cache and "id" in track_cache[key]:
                            found = True
                            break

                    if not found:
                        missing_tracks.append(track)

                if missing_tracks:
                    episodes_with_missing.append({
                        'episode': episode,
                        'missing': missing_tracks
                    })

            if episodes_with_missing:
                from rich.text import Text
                total_missing = sum(len(item['missing']) for item in episodes_with_missing)
                console.print(f"[yellow]Found {total_missing} track(s) without Spotify IDs across {len(episodes_with_missing)} episode(s):[/yellow]\n")

                for item in episodes_with_missing:
                    episode = item['episode']
                    missing = item['missing']

                    # Create clickable episode ID
                    episode_id_link = Text(episode.episode_id, style="bold cyan")
                    episode_id_link.stylize(f"link {episode.link}")
                    console.print(episode_id_link, f" - {episode.title}", sep="")
                    episode_duration = f" | Duration: {episode.duration}" if episode.duration else ""
                    console.print(f"[dim]  Published: {episode.published}{episode_duration}[/dim]")
                    console.print(f"[dim]  Missing: {len(missing)}/{len(episode.tracklist)} tracks[/dim]\n")

                    for i, track in enumerate(missing, 1):
                        track_display = f"{track.artist} - {track.title}"
                        if track.variant:
                            track_display += f" [dim]({track.variant})[/dim]"
                        console.print(f"  {i}. {track_display}")
                    console.print()
            else:
                console.print("[green]✓ All tracks have been found on Spotify[/green]\n")

    # Section 4: Episode title processing (full vs cleaned)
    if show_titles:
        console.print("[bold]4. Episode Title Processing:[/bold]\n")
        console.print("[dim]Shows how episode titles are cleaned (removing episode numbers and podcast name)[/dim]\n")

        episodes = cache.get_all_episodes()
        # Show oldest first for chronological view
        episodes = sorted(episodes, key=lambda ep: ep.id)

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Type", justify="center", width=4)
        table.add_column("ID", style="green", justify="right", width=6)
        table.add_column("Full Title (RSS)", style="white", no_wrap=False, overflow="fold")
        table.add_column("Processed Title", style="cyan", no_wrap=False, overflow="fold")

        for episode in episodes:
            # Get episode type icon
            type_icon = "🎧" if episode.episode_type == "TGL" else "🎁"

            # Show full title vs processed title
            full_title = episode.full_title
            processed_title = episode.title if episode.title else "[dim](empty)[/dim]"

            table.add_row(type_icon, episode.episode_id, full_title, processed_title)

        console.print(table)
        console.print(f"\n[dim]Total: {len(episodes)} episodes[/dim]")
        console.print(f"[dim]💡 Full titles show the original RSS title, processed titles show the cleaned version[/dim]\n")

    console.print("[bold cyan]" + "═" * 70)
    console.print("[bold cyan]End of Report")
    console.print("[bold cyan]" + "═" * 70 + "\n")


# Config command group
config_app = typer.Typer(help="Manage TGL configuration")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show():
    """Show current configuration settings"""
    console.print("\n[bold cyan]" + "═" * 60)
    console.print("[bold cyan]TGL Configuration")
    console.print("[bold cyan]" + "═" * 60 + "\n")

    # Show current settings
    console.print("[bold]Current Settings:[/bold]\n")

    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="white")
    table.add_column("Source", style="dim")

    # Determine source for each setting
    config_exists = paths.config_file.exists()
    env_file_exists = Path(".env").exists()

    # Load config file if it exists
    config_data = {}
    if config_exists:
        with open(paths.config_file, 'rb') as f:
            config_data = tomllib.load(f)

    # Load .env file if it exists (before Settings loads it)
    dotenv_data = {}
    if env_file_exists:
        try:
            with open(".env", 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key_part, value_part = line.split('=', 1)
                        dotenv_data[key_part.strip()] = value_part.strip()
        except:
            pass

    # Check each setting
    settings_info = [
        ("patreon_rss_url", settings.patreon_rss_url),
        ("spotify_client_id", settings.spotify_client_id),
        ("spotify_client_secret", "***" if settings.spotify_client_secret else ""),
        ("spotify_redirect_uri", settings.spotify_redirect_uri),
        ("spotify_playlist_name", settings.spotify_playlist_name),
        ("data_dir", str(paths.data_dir) if settings.data_dir else "default"),
    ]

    for key, value in settings_info:
        # Determine source (check in priority order)
        env_key = key.upper()
        tgl_env_key = f"TGL_{env_key}"

        source = "default"

        # Check actual environment (not from .env)
        # We need to check if it was set BEFORE Settings loaded .env
        # The easiest way is to check if it's in dotenv_data - if not there, it must be from real env
        if tgl_env_key in os.environ:
            # Check if it's from .env or real environment
            if tgl_env_key not in dotenv_data:
                source = "environment"
            elif key in config_data:
                # Both .env and config have it, .env wins (lower priority in our sources order)
                # Actually wait, env_settings comes before dotenv_settings, so real env > config > .env
                source = ".env file"
            else:
                source = ".env file"
        elif env_key in os.environ:
            # Check if it's from .env or real environment
            if env_key not in dotenv_data:
                source = "environment"
            elif key in config_data:
                source = ".env file"
            else:
                source = ".env file"
        elif key in config_data:
            source = "config file"
        elif env_key in dotenv_data or tgl_env_key in dotenv_data:
            source = ".env file"

        # Mask sensitive values
        display_value = value
        if "secret" in key.lower() or "url" in key.lower():
            if value:
                display_value = value[:20] + "..." if len(value) > 20 else value

        table.add_row(key, display_value, source)

    console.print(table)
    console.print()

    # Show file locations
    console.print("[bold]Configuration Files:[/bold]\n")
    locations = Table(show_header=True, header_style="bold cyan", box=None)
    locations.add_column("Type", style="cyan")
    locations.add_column("Location", style="white")
    locations.add_column("Status", style="dim")

    locations.add_row(
        "Config File",
        str(paths.config_file),
        "[green]exists[/green]" if config_exists else "[dim]not found[/dim]"
    )
    locations.add_row(
        "Data Directory",
        str(paths.data_dir),
        "[green]exists[/green]"
    )
    locations.add_row(
        ".env File",
        str(Path(".env").absolute()),
        "[green]exists[/green]" if env_file_exists else "[dim]not found[/dim]"
    )

    console.print(locations)
    console.print()


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Configuration key to set"),
    value: str = typer.Argument(..., help="Value to set")
):
    """Set a configuration value in the config file"""
    # Valid keys (data_dir excluded - must be set via environment variable)
    valid_keys = [
        "patreon_rss_url",
        "spotify_client_id",
        "spotify_client_secret",
        "spotify_redirect_uri",
        "spotify_playlist_name",
    ]

    if key not in valid_keys:
        console.print(f"[red]Error: Invalid configuration key '{key}'[/red]")
        console.print(f"[yellow]Valid keys:[/yellow] {', '.join(valid_keys)}")
        if key == "data_dir":
            console.print(f"[yellow]Note:[/yellow] data_dir must be set via environment variable (TGL_DATA_DIR) or .env file")
        raise typer.Exit(1)

    # Load existing config or create new one
    config_data = {}
    if paths.config_file.exists():
        with open(paths.config_file, 'rb') as f:
            config_data = tomllib.load(f)

    # Update value
    config_data[key] = value

    # Write back
    with open(paths.config_file, 'wb') as f:
        tomli_w.dump(config_data, f)

    console.print(f"\n[green]✓[/green] Set [cyan]{key}[/cyan] in config file")
    console.print(f"[dim]Config file: {paths.config_file}[/dim]\n")


@config_app.command("unset")
def config_unset(
    key: str = typer.Argument(..., help="Configuration key to unset")
):
    """Remove a configuration value from the config file"""
    if not paths.config_file.exists():
        console.print("[yellow]Config file does not exist[/yellow]")
        raise typer.Exit(0)

    # Load existing config
    with open(paths.config_file, 'rb') as f:
        config_data = tomllib.load(f)

    if key not in config_data:
        console.print(f"[yellow]Key '{key}' not found in config file[/yellow]")
        raise typer.Exit(0)

    # Remove key
    del config_data[key]

    # Write back
    with open(paths.config_file, 'wb') as f:
        tomli_w.dump(config_data, f)

    console.print(f"\n[green]✓[/green] Removed [cyan]{key}[/cyan] from config file")
    console.print(f"[dim]Config file: {paths.config_file}[/dim]\n")


@config_app.command("edit")
def config_edit():
    """Open configuration file in default editor"""
    # Create config file if it doesn't exist
    if not paths.config_file.exists():
        console.print("[cyan]Creating new config file...[/cyan]")
        # Create with example content
        example_config = {
            "# Remove the '#' prefix and update values below": None,
            "# patreon_rss_url": "https://www.patreon.com/rss/...",
            "# spotify_client_id": "your_client_id",
            "# spotify_client_secret": "your_client_secret",
            "# spotify_redirect_uri": "http://127.0.0.1:8888/callback",
            "# spotify_playlist_name": "TGL",
        }
        # Write a basic template
        with open(paths.config_file, 'w') as f:
            f.write("# TGL Configuration File\n")
            f.write("# Uncomment and set values below\n\n")
            f.write("# patreon_rss_url = \"https://www.patreon.com/rss/...\"\n")
            f.write("# spotify_client_id = \"your_client_id\"\n")
            f.write("# spotify_client_secret = \"your_client_secret\"\n")
            f.write("# spotify_redirect_uri = \"http://127.0.0.1:8888/callback\"\n")
            f.write("# spotify_playlist_name = \"TGL\"\n")

    console.print(f"[cyan]Opening config file in editor...[/cyan]")
    console.print(f"[dim]{paths.config_file}[/dim]\n")

    # Determine editor
    editor = os.environ.get('EDITOR', 'nano')

    try:
        subprocess.run([editor, str(paths.config_file)], check=True)
        console.print("\n[green]✓[/green] Config file saved")
    except subprocess.CalledProcessError:
        console.print("\n[red]Error: Editor exited with error[/red]")
        raise typer.Exit(1)
    except FileNotFoundError:
        console.print(f"\n[red]Error: Editor '{editor}' not found[/red]")
        console.print(f"[yellow]Set EDITOR environment variable or edit manually:[/yellow]")
        console.print(f"[dim]{paths.config_file}[/dim]")
        raise typer.Exit(1)


@config_app.command("path")
def config_path(
    show_all: bool = typer.Option(False, "--all", help="Show all paths")
):
    """Show configuration file path"""
    if show_all:
        console.print("\n[bold cyan]TGL Directory Paths[/bold cyan]\n")

        table = Table(show_header=True, header_style="bold cyan", box=None)
        table.add_column("Path Type", style="cyan")
        table.add_column("Location", style="white")

        table.add_row("Config Directory", str(paths.config_dir))
        table.add_row("Config File", str(paths.config_file))
        table.add_row("Data Directory", str(paths.data_dir))
        table.add_row("Episodes Cache", str(paths.episodes_cache))
        table.add_row("Search Index", str(paths.search_index_dir))
        table.add_row("Spotify State", str(paths.data_dir / "spotify.json"))

        console.print(table)
        console.print()
    else:
        console.print(str(paths.config_file))


@config_app.command("init")
def config_init():
    """Initialize a new configuration file with prompts"""
    console.print("\n[bold cyan]" + "═" * 60)
    console.print("[bold cyan]Welcome to TGL Configuration")
    console.print("[bold cyan]" + "═" * 60 + "\n")

    if paths.config_file.exists():
        console.print("[yellow]Config file already exists.[/yellow]")
        if not typer.confirm("Overwrite existing config?"):
            raise typer.Exit(0)

    console.print("[bold]Required Configuration:[/bold]\n")

    config_data = {}

    # Patreon RSS URL (required)
    while True:
        patreon_url = typer.prompt("Patreon RSS URL (required)")
        if patreon_url.strip():
            config_data["patreon_rss_url"] = patreon_url.strip()
            break
        console.print("[red]Patreon RSS URL is required to use TGL[/red]")

    # Spotify settings (optional)
    console.print("\n[bold]Optional - Spotify Integration:[/bold]")
    console.print("[dim]Skip this section if you don't plan to use Spotify features[/dim]\n")

    if typer.confirm("Configure Spotify integration?", default=False):
        spotify_id = typer.prompt("Spotify Client ID", default="", show_default=False)
        if spotify_id:
            config_data["spotify_client_id"] = spotify_id

        spotify_secret = typer.prompt("Spotify Client Secret", default="", show_default=False, hide_input=True)
        if spotify_secret:
            config_data["spotify_client_secret"] = spotify_secret

        spotify_uri = typer.prompt("Spotify Redirect URI", default="http://127.0.0.1:8888/callback")
        if spotify_uri:
            config_data["spotify_redirect_uri"] = spotify_uri

    # Write config file with comments
    config_content = """# TGL (The Guestlist) Configuration File
# This file uses TOML format: https://toml.io
#
# You can edit this file directly or use: tgl config set <key> <value>
# View current config: tgl config show

# ====== REQUIRED CONFIGURATION ======

"""

    # Add patreon_rss_url
    config_content += f'patreon_rss_url = "{config_data["patreon_rss_url"]}"\n\n'

    config_content += """# ====== SPOTIFY INTEGRATION ======

# Spotify API Credentials (only needed for 'tgl spotify' command)
# Create an app at: https://developer.spotify.com/dashboard
"""

    if "spotify_client_id" in config_data:
        config_content += f'spotify_client_id = "{config_data["spotify_client_id"]}"\n'
    else:
        config_content += '# spotify_client_id = "your_spotify_client_id"\n'

    if "spotify_client_secret" in config_data:
        config_content += f'spotify_client_secret = "{config_data["spotify_client_secret"]}"\n'
    else:
        config_content += '# spotify_client_secret = "your_spotify_client_secret"\n'

    if "spotify_redirect_uri" in config_data:
        config_content += f'spotify_redirect_uri = "{config_data["spotify_redirect_uri"]}"\n\n'
    else:
        config_content += '# spotify_redirect_uri = "http://127.0.0.1:8888/callback"\n\n'

    config_content += """# ====== SPOTIFY PLAYLIST CONFIGURATION ======

# Episode Playlist Title and Description
# Used when creating playlists for individual episodes
# Placeholders: {id} = episode ID (e.g., E390), {title} = episode title
# spotify_episode_playlist_format = "TGL {id}: {title}"
# spotify_episode_playlist_description = "Tracks from {id}: {title}"

# Year Playlist Title and Description
# Used when creating playlists for all episodes from a specific year
# Placeholder: {year} = year (e.g., 2024)
# spotify_year_playlist_format = "The {year} Sound of The Guestlist by Fear of Tigers"
# spotify_year_playlist_description = "All tracks from The Guestlist episodes published in {year}"

# All-Tracks Playlist Title and Description
# Used when creating the master playlist with all tracks
# spotify_all_playlist_format = "The Sound of The Guestlist by Fear of Tigers"
# spotify_all_playlist_description = "All tracks from every episode of The Guestlist podcast"
"""

    # Write the config file
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.config_file, 'w') as f:
        f.write(config_content)

    console.print(f"\n[green]✓[/green] Configuration saved to:")
    console.print(f"[cyan]{paths.config_file}[/cyan]\n")
    console.print("[dim]The config file includes all available options with helpful comments.[/dim]")
    console.print("[dim]You can edit it directly or use: [cyan]tgl config set <key> <value>[/cyan][/dim]\n")


@app.command(hidden=True)
def cover(text: Optional[str] = typer.Argument(None)):
    """Secret command to generate playlist cover art for testing"""
    from .cover import display_cover_inline

    display_cover_inline(text)


# ══════════════════════════════════════════════════════════════
# Metadata Management Commands
# ══════════════════════════════════════════════════════════════

metadata_app = typer.Typer(help="Manage episode metadata")
app.add_typer(metadata_app, name="metadata")


@metadata_app.command(name="get")
def metadata_get(
    episode_id: str = typer.Argument(..., help="Episode ID (e.g., E390, B05)"),
    field: Optional[str] = typer.Argument(None, help="Specific field to get (optional)"),
):
    """Show episode metadata

    Examples:
        tgl metadata get E390           # Show all metadata
        tgl metadata get E390 title     # Show just the title
        tgl metadata get B05 episode_type  # Show episode type
    """
    from .cache import MetadataCache
    from .models import parse_episode_id
    import json

    cache = MetadataCache()
    episodes = cache.get_all_episodes()

    # Find the episode
    episode = None
    for ep in episodes:
        if ep.episode_id == episode_id.upper():
            episode = ep
            break

    if not episode:
        console.print(f"[red]Episode {episode_id} not found[/red]")
        raise typer.Exit(1)

    # If field specified, show just that field
    if field:
        if not hasattr(episode, field):
            console.print(f"[red]Field '{field}' does not exist[/red]")
            console.print(f"[dim]Available fields: {', '.join(episode.model_fields.keys())}[/dim]")
            raise typer.Exit(1)

        value = getattr(episode, field)
        is_manual = field in episode.manual_overrides

        console.print(f"\n[bold]{episode.episode_id}[/bold] - [cyan]{field}[/cyan]:")
        # Handle different value types
        if value is None:
            console.print(f"  [dim]None[/dim]")
        elif isinstance(value, str):
            console.print(f"  {value}")
        elif isinstance(value, (int, float, bool)):
            console.print(f"  {value}")
        else:
            # For lists, dicts, sets, and other complex types
            console.print(json.dumps(value, indent=2, default=str))

        if is_manual:
            console.print(f"  [yellow]⚠ Manually overridden[/yellow]")
        console.print()
        return

    # Show all metadata
    console.print(f"\n[bold cyan]═══ Episode Metadata: {episode.episode_id} ═══[/bold cyan]\n")

    # Format fields nicely
    fields_to_show = [
        ('episode_id', 'Episode ID'),
        ('id', 'Internal ID'),
        ('title', 'Title'),
        ('episode_type', 'Type'),
        ('published', 'Published'),
        ('year', 'Year'),
        ('duration', 'Duration'),
        ('link', 'Patreon Link'),
        ('guid', 'RSS GUID'),
        ('audio_url', 'Audio URL'),
        ('audio_size', 'Audio Size (bytes)'),
    ]

    for field_name, label in fields_to_show:
        value = getattr(episode, field_name)
        is_manual = field_name in episode.manual_overrides
        manual_indicator = " [yellow]⚠[/yellow]" if is_manual else ""

        if value is not None:
            if field_name == 'link':
                console.print(f"[cyan]{label}:{manual_indicator}[/cyan] [link={value}]{value}[/link]")
            elif field_name == 'audio_url' and value:
                short_url = value[:60] + "..." if len(value) > 60 else value
                console.print(f"[cyan]{label}:{manual_indicator}[/cyan] {short_url}")
            else:
                console.print(f"[cyan]{label}:{manual_indicator}[/cyan] {value}")

    # Show tracklist count
    if episode.tracklist:
        is_manual = 'tracklist' in episode.manual_overrides
        manual_indicator = " [yellow]⚠[/yellow]" if is_manual else ""
        console.print(f"[cyan]Tracks:{manual_indicator}[/cyan] {len(episode.tracklist)}")

    # Show manual overrides if any
    if episode.manual_overrides:
        console.print(f"\n[yellow]Manually overridden fields:[/yellow] {', '.join(sorted(episode.manual_overrides))}")

    console.print()


@metadata_app.command(name="set")
def metadata_set(
    episode_id: str = typer.Argument(..., help="Episode ID (e.g., E390, B05)"),
    field: str = typer.Argument(..., help="Field to set"),
    value: str = typer.Argument(..., help="New value"),
):
    """Set episode metadata field manually

    Changing episode_type will trigger ID recalculation.
    Changing episode_id requires valid format and checks for duplicates.

    Examples:
        tgl metadata set E390 episode_type BONUS     # Change type to BONUS
        tgl metadata set B05 episode_type TGL        # Change type to TGL
        tgl metadata set E390 episode_id E395        # Change episode number
        tgl metadata set E390 title "New Title"      # Change title
    """
    from .cache import MetadataCache
    from .models import parse_episode_id

    cache = MetadataCache()
    episodes = cache.get_all_episodes()

    # Find the episode
    episode = None
    episode_idx = None
    for idx, ep in enumerate(episodes):
        if ep.episode_id == episode_id.upper():
            episode = ep
            episode_idx = idx
            break

    if not episode:
        console.print(f"[red]Episode {episode_id} not found[/red]")
        raise typer.Exit(1)

    # Validate field exists
    if field not in episode.model_fields:
        console.print(f"[red]Field '{field}' does not exist[/red]")
        console.print(f"[dim]Available fields: {', '.join(episode.model_fields.keys())}[/dim]")
        raise typer.Exit(1)

    # Restricted fields that can't be set manually
    restricted_fields = {'id', 'manual_overrides'}
    if field in restricted_fields:
        console.print(f"[red]Field '{field}' cannot be set manually[/red]")
        raise typer.Exit(1)

    old_value = getattr(episode, field)

    # Special handling for episode_type changes
    if field == 'episode_type':
        if value.upper() not in ['TGL', 'BONUS']:
            console.print(f"[red]episode_type must be 'TGL' or 'BONUS'[/red]")
            raise typer.Exit(1)

        value = value.upper()

        if value == old_value:
            console.print(f"[yellow]episode_type is already {value}[/yellow]")
            return

        console.print(f"\n[yellow]Changing episode_type from {old_value} to {value}[/yellow]")
        console.print("[dim]This will trigger ID recalculation...[/dim]\n")

        # Change the type
        episode.episode_type = value
        episode.manual_overrides.add('episode_type')

        # Recalculate IDs for all episodes
        _recalculate_episode_ids(episodes)

        # Save updated cache
        cache._save_cache(episodes)

        # Find the episode again (it may have a new ID)
        new_ep = None
        for ep in episodes:
            if ep.guid == episode.guid:
                new_ep = ep
                break

        if new_ep:
            console.print(f"[green]✓[/green] Episode type changed to {value}")
            console.print(f"[green]✓[/green] New episode ID: {new_ep.episode_id}")
        else:
            console.print(f"[red]Failed to find episode after recalculation[/red]")
            raise typer.Exit(1)

        return

    # Special handling for episode_id changes
    if field == 'episode_id':
        new_id = value.upper()

        # Validate format
        try:
            numeric_id = parse_episode_id(new_id)
        except ValueError as e:
            console.print(f"[red]Invalid episode ID format: {e}[/red]")
            raise typer.Exit(1)

        # Check for duplicates
        for ep in episodes:
            if ep.episode_id == new_id and ep.guid != episode.guid:
                console.print(f"[red]Episode ID {new_id} already exists[/red]")
                raise typer.Exit(1)

        console.print(f"\n[yellow]Changing episode_id from {episode.episode_id} to {new_id}[/yellow]\n")

        # Update episode_type based on new ID
        if new_id.startswith('E'):
            episode.episode_type = 'TGL'
            episode.id = numeric_id
        elif new_id.startswith('B'):
            episode.episode_type = 'BONUS'
            episode.id = numeric_id

        episode.episode_id = new_id
        episode.manual_overrides.add('episode_id')
        episode.manual_overrides.add('episode_type')  # Type is implicitly set too

        # Update full_title
        if episode.episode_type == 'TGL':
            episode.full_title = f"TGL {new_id}: {episode.title}"
        else:
            episode.full_title = f"BONUS {new_id}: {episode.title}"

        # Save
        episodes[episode_idx] = episode
        cache._save_cache(episodes)

        console.print(f"[green]✓[/green] Episode ID changed to {new_id}")
        console.print(f"[green]✓[/green] Episode type set to {episode.episode_type}")
        return

    # Handle other fields
    # Convert value to appropriate type
    field_type = episode.model_fields[field].annotation

    try:
        if field_type == int or 'int' in str(field_type):
            value = int(value)
        elif field_type == bool or 'bool' in str(field_type):
            value = value.lower() in ('true', '1', 'yes', 'y')
        # else keep as string
    except (ValueError, TypeError) as e:
        console.print(f"[red]Invalid value type: {e}[/red]")
        raise typer.Exit(1)

    # Set the value
    setattr(episode, field, value)
    episode.manual_overrides.add(field)

    # Save
    episodes[episode_idx] = episode
    cache._save_cache(episodes)

    console.print(f"\n[green]✓[/green] {field} changed from [dim]{old_value}[/dim] to [bold]{value}[/bold]")
    console.print(f"[dim]This field is now marked as manually overridden[/dim]\n")


def _recalculate_episode_ids(episodes: list):
    """Recalculate episode IDs after type changes

    Uses inference to assign proper TGL episode numbers based on chronological position.
    BONUS episodes are renumbered sequentially.
    """
    from .fetcher import PatreonPodcastFetcher

    # Create a temporary fetcher for inference
    fetcher = PatreonPodcastFetcher('')

    # Separate TGL and BONUS
    tgl_episodes = [ep for ep in episodes if ep.episode_type == 'TGL']
    bonus_episodes = [ep for ep in episodes if ep.episode_type == 'BONUS']

    # Sort TGL by published date for inference
    tgl_episodes.sort(key=lambda ep: ep.published)

    # Build temp episode dict for inference (mimics fetcher's structure)
    temp_tgl = []
    for ep in tgl_episodes:
        # Try to parse episode number from title
        ep_num = fetcher.parse_episode_id(ep.full_title)
        temp_tgl.append({
            'title': ep.full_title,
            'guid': ep.guid,
            'link': ep.link,
            'parsed_num': ep_num
        })

    # Run inference on TGL episodes
    inferred_numbers = fetcher._infer_episode_numbers(temp_tgl)

    # Assign TGL episode numbers
    for idx, ep in enumerate(tgl_episodes):
        # Skip if manually set
        if 'episode_id' in ep.manual_overrides:
            continue

        # Get inferred number (or use parsed number from title)
        inferred_num = inferred_numbers.get(ep.link)
        if inferred_num is None:
            # Try to parse from title
            inferred_num = fetcher.parse_episode_id(ep.full_title)

        if inferred_num:
            ep.id = inferred_num
            ep.episode_id = f"E{inferred_num}"
            # Don't override full_title - keep original RSS title

    # Sort BONUS by published date
    bonus_episodes.sort(key=lambda ep: ep.published)

    # Renumber BONUS episodes
    for idx, ep in enumerate(bonus_episodes, start=1):
        # Only update if not manually set
        if 'episode_id' not in ep.manual_overrides:
            ep.id = 10000 + idx
            ep.episode_id = f"B{idx:02d}"
            # Don't override full_title - keep original RSS title


@app.command()
def analyse(
    episode_id: Optional[str] = typer.Argument(None, help="Optional episode ID to analyze tracks from (e.g., E390, B01)")
):
    """Analyze tracks across episodes and gather Last.fm tags

    This command:
    - Maps tracks to the episodes they appear in
    - Fetches Last.fm tags for all tracks
    - Stores results in tracks.json (acts as a cache)

    Pass an episode ID to only analyze tracks from that specific episode (useful for testing).

    Examples:
      tgl analyse           # Analyze all tracks
      tgl analyse E390      # Only analyze tracks from episode E390
    """
    from .analysis import TrackAnalyzer

    console.print("\n[bold cyan]═══ Track Analysis ═══[/bold cyan]")

    # Load episode cache
    cache = MetadataCache()
    if cache.should_auto_refresh():
        from .fetcher import PatreonPodcastFetcher
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    all_episodes = cache.get_all_episodes()

    # Filter episodes if episode_id is provided
    if episode_id:
        # Find the episode by ID or GUID (same as get command)
        filtered_episode, _ = find_episode_by_id_or_guid(all_episodes, episode_id)

        if not filtered_episode:
            console.print(f"[red]Error: Episode {episode_id} not found[/red]")
            raise typer.Exit(1)

        console.print(f"[dim]Filtering to tracks from episode {filtered_episode.episode_id}: {filtered_episode.title}[/dim]")
        episodes_to_analyze = [filtered_episode]
    else:
        episodes_to_analyze = all_episodes

    # Initialize analyzer
    analyzer = TrackAnalyzer(settings)

    # Build track-to-episode mapping and get list of track keys to analyze
    track_keys = analyzer.build_episode_mapping(episodes_to_analyze)

    # Check if Last.fm API key is configured
    if not settings.lastfm_api_key:
        console.print("\n[yellow]⚠ Last.fm API key not configured - skipping tags analysis[/yellow]")
        console.print("[dim]To analyze track tags, configure Last.fm API key with:[/dim]")
        console.print("[dim]  [cyan]tgl config set lastfm_api_key YOUR_KEY[/cyan][/dim]")
        console.print("[dim]Get your API key at: https://www.last.fm/api/account/create[/dim]\n")
        analyzer.print_summary()
        raise typer.Exit(0)

    # Fetch Last.fm tags (only for tracks from the filtered episodes)
    analyzer.fetch_lastfm_tags(track_keys_filter=track_keys)

    # Print summary
    analyzer.print_summary()


@app.command(name="analyze", hidden=True)
def analyze_alias(
    episode_id: Optional[str] = typer.Argument(None, help="Optional episode ID to analyze tracks from (e.g., E390, B01)")
):
    """Alias for analyse command (American spelling)"""
    analyse(episode_id)


def main():
    """Main CLI entry point"""
    app()


if __name__ == "__main__":
    main()
