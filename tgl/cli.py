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
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text
import requests

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

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Fetching episodes from RSS feed...", total=None)
        episodes = fetcher.fetch_episodes()
        progress.update(task, completed=True, total=1)

    console.print(f"[green]✓[/green] Fetched {len(episodes)} episodes\n")

    # Update cache
    for episode in episodes:
        cache.add_episode(episode)

    cache.save()

    # Build search index
    console.print("[cyan]Building search index...[/cyan]")
    search_index = SearchIndex(cache.cache_dir)
    search_index.build_index(cache.episodes)
    console.print(f"[green]✓[/green] Search index built\n")

    console.print(f"[bold green]✓ Done![/bold green] Cached {len(episodes)} episodes")
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
    bonus: bool = typer.Option(False, "--bonus", help="Show only BONUS episodes")
):
    """List all episodes"""
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
    elif bonus and not tgl:
        episodes = [ep for ep in episodes if ep.episode_type == 'BONUS']
    # If both or neither, show all

    if not episodes:
        console.print(f"[yellow]No episodes found[/yellow]")
        raise typer.Exit(1)

    # Show overview
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
            tgl = year_stats[yr]['TGL']
            bonus = year_stats[yr]['BONUS']
            total = tgl + bonus
            total_tgl += tgl
            total_bonus += bonus
            overview_table.add_row(str(yr), str(tgl), str(bonus), str(total))

        # Add totals row
        overview_table.add_row(
            "[bold]Total[/bold]",
            f"[bold]{total_tgl}[/bold]",
            f"[bold]{total_bonus}[/bold]",
            f"[bold]{total_tgl + total_bonus}[/bold]"
        )

        console.print(overview_table)
    console.print()

    table = Table(title=f"TGL Episodes{f' ({year})' if year else ''}", show_header=True, header_style="bold cyan")
    table.add_column("Type", justify="center", width=4)
    table.add_column("ID", style="green", justify="right", width=6)
    table.add_column("Title", style="white", no_wrap=False, overflow="fold")
    table.add_column("Tracks", style="dim", justify="center", width=6)
    table.add_column("Date", style="yellow", width=12)

    for episode in episodes:
        # Create clickable episode ID
        clickable_id = Text(episode.episode_id)
        clickable_id.stylize(f"link {episode.link}")

        # Get episode type icon
        type_icon = "🎧" if episode.episode_type == "TGL" else "🎁"

        # Get track count
        track_count = str(len(episode.tracklist)) if episode.tracklist else "-"

        table.add_row(type_icon, clickable_id, episode.title, track_count, episode.published)

    console.print(table)
    console.print(f"\n[dim]Total: {len(episodes)} episodes[/dim]")
    console.print(f"[dim]💡 Tip: Click on episode IDs to open in browser[/dim]\n")


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
    console.print(f"[dim]Published: {episode.published}[/dim]")

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


@app.command()
def download(episode_id: str):
    """Download an episode audio file"""
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

    if not episode.audio_url:
        console.print(f"[red]No audio URL found for episode {episode_id}[/red]")
        raise typer.Exit(1)

    # Create episodes directory
    episodes_dir = Path("episodes")
    episodes_dir.mkdir(exist_ok=True)

    # Create filename
    filename = f"TGL #{episode.id} - {episode.title}.mp3"
    # Clean filename
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filepath = episodes_dir / filename

    if filepath.exists():
        console.print(f"[yellow]File already exists: {filepath}[/yellow]")
        if not typer.confirm("Overwrite?"):
            raise typer.Exit(0)

    console.print(f"\n[cyan]Downloading: {episode.full_title}[/cyan]")
    console.print(f"[dim]Saving to: {filepath}[/dim]\n")

    try:
        response = requests.get(episode.audio_url, stream=True, timeout=30)
        response.raise_for_status()

        total_size = int(response.headers.get('content-length', 0))

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Downloading...", total=total_size)

            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

        console.print(f"\n[bold green]✓ Downloaded successfully![/bold green]")
        console.print(f"[dim]Saved to: {filepath}[/dim]\n")

    except requests.exceptions.RequestException as e:
        console.print(f"\n[red]Error downloading episode: {e}[/red]")
        if filepath.exists():
            filepath.unlink()
        raise typer.Exit(1)


@app.command()
def spotify(
    episodes: Optional[List[str]] = typer.Option(None, "--episode", help="Create playlist for specific episode (can be used multiple times)"),
    years: Optional[List[int]] = typer.Option(None, "--year", help="Create playlist for all tracks from a year (can be used multiple times)"),
    all_years: bool = typer.Option(False, "--years", help="Create playlists for all years with episodes"),
    all_tracks: bool = typer.Option(False, "--all", help="Create playlist with ALL tracks from all episodes"),
    sync: bool = typer.Option(False, "--sync", help="Update all playlists currently tracked in state"),
    dry_run: bool = typer.Option(False, "-n", "--dry-run", help="Dry run mode (no write operations)"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show all Spotify API calls"),
):
    """Manage Spotify playlists for TGL episodes

    Run without arguments to authorize Spotify access.
    Multiple playlist options can be combined:

      tgl spotify --episode E390 --year 2024 --all

    This will create/update all three playlists in sequence.

    Use --years to create playlists for all years:

      tgl spotify --years

    Use --sync to update all playlists currently tracked in state:

      tgl spotify --sync
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

    # Validate that --years and --year are mutually exclusive
    if all_years and years:
        console.print("\n[red]Error: --years cannot be used with --year[/red]\n")
        console.print("[dim]Use --years to create playlists for all years, or --year to specify individual years[/dim]\n")
        raise typer.Exit(1)

    # Validate that --sync is mutually exclusive with other playlist options
    if sync and (episodes or years or all_years or all_tracks):
        console.print("\n[red]Error: --sync cannot be used with --episode, --year, --years, or --all[/red]\n")
        console.print("[dim]Use --sync alone to update all tracked playlists, or specify individual playlists without --sync[/dim]\n")
        raise typer.Exit(1)

    # Initialize Spotify manager
    from .spotify import SpotifyManager
    spotify_manager = SpotifyManager(settings, dry_run=dry_run, verbose=verbose)

    # If no options provided, just run authorization
    if not episodes and not years and not all_years and not all_tracks and not sync:
        if spotify_manager.authorize():
            console.print("[green]✓ Spotify authorization successful[/green]")
            console.print("[dim]You can now use Spotify commands like:[/dim]")
            console.print("[dim]  [cyan]tgl spotify --episode E390[/cyan][/dim]")
            console.print("[dim]  [cyan]tgl spotify --year 2024[/cyan][/dim]")
            console.print("[dim]  [cyan]tgl spotify --years[/cyan] (all years)[/dim]")
            console.print("[dim]  [cyan]tgl spotify --all[/cyan][/dim]")
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
                        playlist_format=settings.spotify_episode_playlist_format
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
                        playlist_format=settings.spotify_year_playlist_format
                    )
                    if not success:
                        all_success = False
                except ValueError as e:
                    console.print(f"[red]Error parsing year {year_str}: {e} (skipping)[/red]")
                    all_success = False

            elif playlist_key == "all":
                success = spotify_manager.sync_all_playlist(
                    all_episodes_list,
                    playlist_format=settings.spotify_all_playlist_format
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
                    playlist_format=settings.spotify_episode_playlist_format
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
                playlist_format=settings.spotify_year_playlist_format
            )
            if not success:
                all_success = False

    # Process all-tracks playlist
    if all_tracks:
        success = spotify_manager.sync_all_playlist(
            all_episodes_list,
            playlist_format=settings.spotify_all_playlist_format
        )
        if not success:
            all_success = False

    if not all_success:
        raise typer.Exit(1)


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

        playlist_name = typer.prompt("Spotify Playlist Name", default="The Sound of The Guestlist by Fear of Tigers")
        if playlist_name:
            config_data["spotify_playlist_name"] = playlist_name

    # Write config file
    with open(paths.config_file, 'wb') as f:
        tomli_w.dump(config_data, f)

    console.print(f"\n[green]✓[/green] Configuration saved to:")
    console.print(f"[cyan]{paths.config_file}[/cyan]\n")
    console.print("[dim]You can update your configuration anytime with: [cyan]tgl config set[/cyan][/dim]\n")


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
