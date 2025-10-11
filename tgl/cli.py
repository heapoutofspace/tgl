"""TGL CLI entry point and commands"""

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
        console.print("  [cyan]info[/cyan]                 Show details for a specific episode")
        console.print("  [cyan]search[/cyan]               Search episodes by title, description, or tracks")
        console.print("  [cyan]download[/cyan]             Download an episode audio file")
        console.print("  [cyan]spotify[/cyan]              Import tracklists to Spotify playlist")
        console.print("  [cyan]update[/cyan]               Update episode metadata from RSS feed")
        console.print("  [cyan]config[/cyan]               Manage TGL configuration\n")

        console.print("[bold]Examples:[/bold]\n")
        console.print("  [dim]# List all episodes[/dim]")
        console.print("  [green]tgl.py list[/green]\n")

        console.print("  [dim]# List only TGL episodes from 2023[/dim]")
        console.print("  [green]tgl.py list --year 2023 --tgl[/green]\n")

        console.print("  [dim]# List only BONUS episodes[/dim]")
        console.print("  [green]tgl.py list --bonus[/green]\n")

        console.print("  [dim]# Show details for episode 390[/dim]")
        console.print("  [green]tgl.py info E390[/green]\n")

        console.print("  [dim]# Show details for bonus episode 5[/dim]")
        console.print("  [green]tgl.py info B05[/green]\n")

        console.print("  [dim]# Search for episodes about house music[/dim]")
        console.print("  [green]tgl.py search \"house music\"[/green]\n")

        console.print("  [dim]# Search for episodes with tracks by LAU[/dim]")
        console.print("  [green]tgl.py search LAU[/green]\n")

        console.print("  [dim]# Update the episode cache[/dim]")
        console.print("  [green]tgl.py update[/green]\n")

        console.print("[dim]For detailed help on any command, use:[/dim]")
        console.print("  [green]tgl.py [command] --help[/green]\n")

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
            cached_path = _get_cached_audio_path(episode.audio_url)
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


def parse_episode_id(episode_id_str: str) -> int:
    """Parse episode ID string to internal numeric ID

    Accepts:
    - Plain numbers: "390" -> 390 (TGL episode)
    - E prefix: "E390" -> 390 (TGL episode)
    - B prefix: "B05" -> 10005 (BONUS episode, 10000 + 5)
    """
    episode_id_str = episode_id_str.strip().upper()

    if episode_id_str.startswith('E'):
        # TGL episode
        try:
            return int(episode_id_str[1:])
        except ValueError:
            raise ValueError(f"Invalid episode ID format: {episode_id_str}")
    elif episode_id_str.startswith('B'):
        # BONUS episode
        try:
            b_number = int(episode_id_str[1:])
            return 10000 + b_number
        except ValueError:
            raise ValueError(f"Invalid episode ID format: {episode_id_str}")
    else:
        # Plain number, assume TGL episode
        try:
            return int(episode_id_str)
        except ValueError:
            raise ValueError(f"Invalid episode ID format: {episode_id_str}")


@app.command(name="info")
@app.command(name="show")
def info(episode_id: str):
    """Show details for a specific episode"""
    cache = MetadataCache()

    # Auto-refresh if cache is stale or empty
    if cache.should_auto_refresh():
        fetcher = PatreonPodcastFetcher(settings.patreon_rss_url)
        cache.refresh(fetcher)

    if not cache.episodes:
        console.print("[red]Error: Could not load episodes[/red]")
        raise typer.Exit(1)

    # Parse episode ID
    try:
        numeric_id = parse_episode_id(episode_id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    episode = cache.get_episode(numeric_id)

    if not episode:
        console.print(f"[red]Episode {episode_id} not found[/red]")
        raise typer.Exit(1)

    # Display episode info with clickable ID
    clickable_id = f"[link={episode.link}]{episode.episode_id}[/link]"
    console.print(f"\n[bold cyan]{clickable_id}:[/bold cyan] [bold white]{episode.title}[/bold white]")
    duration_info = f" | Duration: {episode.duration}" if episode.duration else ""
    console.print(f"[dim]Published: {episode.published}{duration_info}[/dim]")

    # Display description text
    if episode.description_text:
        console.print(f"\n[bold]Description:[/bold]")
        # Limit to first 500 chars to keep it concise
        desc = episode.description_text
        if len(desc) > 500:
            desc = desc[:500] + "..."
        console.print(f"[dim]{desc}[/dim]")

    # Display structured tracklist
    if episode.tracklist:
        console.print(f"\n[bold]Tracklist ({len(episode.tracklist)} tracks):[/bold]")
        for i, track in enumerate(episode.tracklist, 1):
            track_display = f"  {i:3d}. {track.artist} - {track.title}"
            if track.variant:
                track_display += f" [dim]({track.variant})[/dim]"
            console.print(track_display)
    else:
        console.print("\n[yellow]No tracklist found[/yellow]")

    console.print()


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


def _get_cached_audio_path(audio_url: str) -> Path:
    """Get the cached audio file path for a given URL

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
            cached_path = _get_cached_audio_path(episode.audio_url)
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
    console.print(f"[dim]Audio cache: {paths.audio_cache_dir}[/dim]\n")


@app.command()
def spotify(
    identifiers: Optional[List[str]] = typer.Argument(None, help="Years (4 digits) or episode IDs (e.g., 2024, 390, E390, B01)"),
    all_years: bool = typer.Option(False, "--years", help="Create playlists for all years with episodes"),
    all_tracks: bool = typer.Option(False, "--all", help="Create playlist with ALL tracks from all episodes"),
    sync: bool = typer.Option(False, "--sync", help="Update all playlists currently tracked in state"),
    dry_run: bool = typer.Option(False, "-n", "--dry-run", help="Dry run mode (no write operations)"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show all Spotify API calls"),
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
    spotify_manager = SpotifyManager(settings, dry_run=dry_run, verbose=verbose)

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
        playlists = spotify_manager.state.get("playlists", {})

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
    section: Optional[str] = typer.Argument(None, help="Section to show: 'missing', 'gaps', 'spotify', or 'all' (default: all)")
):
    """Diagnose issues with episode metadata and Spotify track mappings

    This command helps identify:
    - Episodes available in RSS feed but missing from metadata cache
    - Tracks in metadata that couldn't be found on Spotify
    """
    import json

    # Normalize section argument
    valid_sections = {'missing', 'gaps', 'spotify', 'all'}
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


def main():
    """Main CLI entry point"""
    app()


if __name__ == "__main__":
    main()
