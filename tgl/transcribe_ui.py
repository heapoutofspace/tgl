"""Textual UI for transcription progress tracking"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Callable, Dict, Any
from pathlib import Path
from datetime import datetime
import threading
from collections import deque
from queue import Queue, Empty

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal, ScrollableContainer
from textual.widgets import Header, Footer, Static, ProgressBar, Label
from textual.reactive import reactive
from rich.text import Text
from rich.table import Table

from .models import Episode


# Message types for communication between transcription worker and TUI
# Only simple Python objects (dicts, strings, numbers) - NO PyTorch tensors!
class TranscriptionMessage:
    """Messages sent from transcription worker to TUI"""

    @staticmethod
    def progress(guid: str, progress: float) -> Dict[str, Any]:
        """Progress update message"""
        return {"type": "progress", "guid": guid, "progress": progress}

    @staticmethod
    def segment(guid: str, text: str) -> Dict[str, Any]:
        """New transcription segment message"""
        return {"type": "segment", "guid": guid, "text": text}

    @staticmethod
    def complete(guid: str, text: str, segments: Optional[list] = None) -> Dict[str, Any]:
        """Transcription complete message"""
        return {"type": "complete", "guid": guid, "text": text, "segments": segments}

    @staticmethod
    def error(guid: str, error: str) -> Dict[str, Any]:
        """Error message"""
        return {"type": "error", "guid": guid, "error": error}


class EpisodeState(Enum):
    """Episode processing states"""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    ERROR = "error"


@dataclass
class EpisodeStatus:
    """Track status of an episode"""
    episode: Episode
    state: EpisodeState = EpisodeState.PENDING
    download_progress: float = 0.0
    transcription_progress: float = 0.0
    error_message: Optional[str] = None
    transcription_text: List[str] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class OverallProgressPanel(Static):
    """Panel showing overall progress statistics"""

    total_episodes = reactive(0)
    completed_episodes = reactive(0)
    failed_episodes = reactive(0)
    current_episode = reactive(None)
    current_transcription_progress = reactive(0.0)

    def compose(self) -> ComposeResult:
        """Compose the panel with progress bar"""
        yield Static(id="overall-text")
        yield ProgressBar(id="transcription-progress-bar", show_eta=False)

    def on_mount(self) -> None:
        """Initialize the progress bar as hidden"""
        progress_bar = self.query_one("#transcription-progress-bar", ProgressBar)
        progress_bar.display = False

    def watch_current_episode(self, episode_id: Optional[str]) -> None:
        """Show/hide progress bar when episode changes"""
        progress_bar = self.query_one("#transcription-progress-bar", ProgressBar)
        if episode_id:
            progress_bar.display = True
        else:
            progress_bar.display = False
        self._update_display()

    def watch_current_transcription_progress(self, progress: float) -> None:
        """Update progress bar when progress changes"""
        progress_bar = self.query_one("#transcription-progress-bar", ProgressBar)
        progress_bar.update(total=100, progress=progress)
        self._update_display()

    def watch_total_episodes(self, total: int) -> None:
        """Update display when total changes"""
        self._update_display()

    def watch_completed_episodes(self, completed: int) -> None:
        """Update display when completed changes"""
        self._update_display()

    def watch_failed_episodes(self, failed: int) -> None:
        """Update display when failed changes"""
        self._update_display()

    def _update_display(self) -> None:
        """Update the text display"""
        progress_pct = (
            (self.completed_episodes / self.total_episodes * 100)
            if self.total_episodes > 0 else 0
        )

        text = Text()
        text.append("Overall Progress\n", style="bold cyan")
        text.append(f"━" * 40 + "\n", style="cyan")
        text.append(f"Total Episodes: {self.total_episodes}\n")
        text.append(f"Completed: ", style="green")
        text.append(f"{self.completed_episodes}\n")
        text.append(f"Failed: ", style="red")
        text.append(f"{self.failed_episodes}\n")
        text.append(f"Progress: {progress_pct:.1f}%\n", style="bold")

        # Show current transcription if active
        if self.current_episode:
            text.append("\n")
            text.append(f"Currently Transcribing: ", style="bold magenta")
            text.append(f"{self.current_episode}\n", style="magenta")

        text_widget = self.query_one("#overall-text", Static)
        text_widget.update(text)


class EpisodeListPanel(Static):
    """Panel showing list of all episodes and their status"""
    
    episodes_status = reactive(dict)
    
    def render(self) -> Table:
        """Render the episode list"""
        table = Table(title="Episodes", show_header=True, header_style="bold cyan", expand=True)
        table.add_column("ID", style="cyan", width=6, no_wrap=True)
        table.add_column("Title", style="white", ratio=1)
        table.add_column("Status", width=20, no_wrap=True)
        
        for guid, status in self.episodes_status.items():
            ep = status.episode
            title = ep.title[:27] + "..." if len(ep.title) > 30 else ep.title
            
            # Status with icon
            if status.state == EpisodeState.PENDING:
                state_str = "⏳ Pending"
                state_style = "dim"
            elif status.state == EpisodeState.DOWNLOADING:
                state_str = f"⬇️  Downloading {status.download_progress:.0f}%"
                state_style = "yellow"
            elif status.state == EpisodeState.DOWNLOADED:
                state_str = "✓ Downloaded"
                state_style = "blue"
            elif status.state == EpisodeState.TRANSCRIBING:
                state_str = f"🎤 Transcribing {status.transcription_progress:.0f}%"
                state_style = "magenta"
            elif status.state == EpisodeState.TRANSCRIBED:
                state_str = "✅ Complete"
                state_style = "green"
            else:  # ERROR
                state_str = "❌ Error"
                state_style = "red"
            
            table.add_row(
                ep.episode_id or str(ep.id),
                title,
                Text(state_str, style=state_style)
            )
        
        return table


class DownloadPanel(Static):
    """Panel showing current download progress"""
    
    current_download = reactive(None)
    download_progress = reactive(0.0)
    download_speed = reactive("")
    
    def render(self) -> Text:
        """Render download panel"""
        text = Text()
        text.append("Downloads\n", style="bold yellow")
        text.append(f"━" * 40 + "\n", style="yellow")
        
        if self.current_download:
            text.append(f"Downloading: {self.current_download}\n")
            text.append(f"Progress: {self.download_progress:.1f}% {self.download_speed}\n")
        else:
            text.append("No active downloads\n", style="dim")
        
        return text


class TranscriptionPanel(ScrollableContainer):
    """Panel showing live transcription text with scrolling"""

    transcription_segments = reactive(list)

    def compose(self) -> ComposeResult:
        """Compose the transcription panel"""
        yield Static(id="transcription-content")

    def watch_transcription_segments(self, segments: List[str]) -> None:
        """Update transcription content when segments change"""
        content_widget = self.query_one("#transcription-content", Static)

        text = Text()
        for segment in segments:
            text.append(segment + "\n")

        content_widget.update(text)

        # Auto-scroll to bottom
        self.scroll_end(animate=False)


class TranscriptionApp(App):
    """Main transcription TUI application"""

    # Disable mouse support to avoid capture issues
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2 4;
        grid-columns: 2fr 1fr;
        grid-rows: auto 1fr 1fr auto;
    }

    #overall-progress {
        column-span: 2;
        height: auto;
        border: solid cyan;
        padding: 1;
    }

    #transcription-panel {
        row-span: 3;
        border: solid magenta;
        padding: 1;
    }

    #episode-list {
        row-span: 2;
        border: solid green;
        padding: 1;
    }

    #download-panel {
        height: auto;
        border: solid yellow;
        padding: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "toggle_dark", "Toggle Dark Mode"),
    ]

    def __init__(
        self,
        episodes: List[Episode],
        transcription_cache,
        download_callback: Optional[Callable] = None,
        results_queue: Optional[Queue] = None
    ):
        super().__init__()
        self.episodes = episodes
        self.transcription_cache = transcription_cache
        self.download_callback = download_callback
        self.results_queue = results_queue
        self.episode_statuses = {
            (ep.guid or str(ep.id)): EpisodeStatus(episode=ep)
            for ep in episodes
        }
        self._workers_started = False
        self._check_timer = None
    
    def compose(self) -> ComposeResult:
        """Create the UI layout"""
        yield Header()
        yield OverallProgressPanel(id="overall-progress")
        yield TranscriptionPanel(id="transcription-panel")
        yield EpisodeListPanel(id="episode-list")
        yield DownloadPanel(id="download-panel")
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the app when mounted"""
        overall = self.query_one("#overall-progress", OverallProgressPanel)
        overall.total_episodes = len(self.episodes)

        # Create new dict to trigger reactive watcher
        episode_list = self.query_one("#episode-list", EpisodeListPanel)
        episode_list.episodes_status = dict(self.episode_statuses)

        # Start download worker threads if callback provided
        if not self._workers_started and self.download_callback:
            self._workers_started = True
            self.download_callback(self)

        # Start timer to check results queue for messages from transcription worker
        if self.results_queue:
            self._check_timer = self.set_interval(0.1, self._check_results_queue)
    
    def update_episode_state(
        self,
        guid: str,
        state: EpisodeState,
        download_progress: Optional[float] = None,
        transcription_progress: Optional[float] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Update an episode's state"""
        if guid in self.episode_statuses:
            status = self.episode_statuses[guid]
            status.state = state

            if download_progress is not None:
                status.download_progress = download_progress
            if transcription_progress is not None:
                status.transcription_progress = transcription_progress
                # Update overall panel if this is the current episode
                overall = self.query_one("#overall-progress", OverallProgressPanel)
                if overall.current_episode == status.episode.episode_id:
                    overall.current_transcription_progress = transcription_progress
                    overall.refresh()
            if error_message is not None:
                status.error_message = error_message

            # Update overall progress
            completed = sum(
                1 for s in self.episode_statuses.values()
                if s.state == EpisodeState.TRANSCRIBED
            )
            failed = sum(
                1 for s in self.episode_statuses.values()
                if s.state == EpisodeState.ERROR
            )

            overall = self.query_one("#overall-progress", OverallProgressPanel)
            overall.completed_episodes = completed
            overall.failed_episodes = failed

            # Clear current transcription from overall panel when episode completes or fails
            if state in (EpisodeState.TRANSCRIBED, EpisodeState.ERROR):
                if overall.current_episode == status.episode.episode_id:
                    overall.current_episode = None
                    overall.current_transcription_progress = 0.0

            overall.refresh()

            # Update episode list
            # Create new dict to trigger reactive watcher and force refresh
            episode_list = self.query_one("#episode-list", EpisodeListPanel)
            episode_list.episodes_status = dict(self.episode_statuses)
            episode_list.refresh()
    
    def add_transcription_segment(self, guid: str, segment_text: str) -> None:
        """Add a new transcription segment"""
        if guid in self.episode_statuses:
            status = self.episode_statuses[guid]
            status.transcription_text.append(segment_text)

            # Update transcription panel
            # IMPORTANT: Create a new list to trigger Textual's reactive property watcher
            transcription = self.query_one("#transcription-panel", TranscriptionPanel)
            transcription.transcription_segments = status.transcription_text.copy()
    
    def set_current_transcription(self, guid: str) -> None:
        """Set the current episode being transcribed"""
        if guid in self.episode_statuses:
            status = self.episode_statuses[guid]

            # Update overall progress panel to show current transcription
            overall = self.query_one("#overall-progress", OverallProgressPanel)
            overall.current_episode = status.episode.episode_id
            overall.current_transcription_progress = 0.0
            overall.refresh()

            # Clear segments when starting new episode
            transcription = self.query_one("#transcription-panel", TranscriptionPanel)
            transcription.transcription_segments = []
    
    def update_download_progress(self, episode_id: str, progress: float, speed: str = "") -> None:
        """Update download progress"""
        download = self.query_one("#download-panel", DownloadPanel)
        download.current_download = episode_id
        download.download_progress = progress
        download.download_speed = speed
        download.refresh()

    def clear_download(self) -> None:
        """Clear current download"""
        download = self.query_one("#download-panel", DownloadPanel)
        download.current_download = None
        download.download_progress = 0.0
        download.download_speed = ""
        download.refresh()

    def check_completion(self) -> None:
        """Check if all episodes are done and exit if so"""
        all_done = all(
            status.state in (EpisodeState.TRANSCRIBED, EpisodeState.ERROR)
            for status in self.episode_statuses.values()
        )
        if all_done:
            # Save final transcriptions before exiting
            self.transcription_cache.save()
            # Exit the app
            self.exit()

    def _check_results_queue(self) -> None:
        """Check results queue for messages from transcription worker"""
        try:
            # Process all available messages (non-blocking)
            while True:
                message = self.results_queue.get_nowait()
                self._process_transcription_message(message)
        except Empty:
            # No more messages, check if we're done
            self.check_completion()

    def _process_transcription_message(self, message: Dict[str, Any]) -> None:
        """Process a message from the transcription worker (only simple Python objects!)"""
        msg_type = message.get("type")
        guid = message.get("guid")

        if msg_type == "start":
            # Transcription starting
            self.update_episode_state(
                guid,
                EpisodeState.TRANSCRIBING,
                transcription_progress=0.0
            )
            self.set_current_transcription(guid)

        elif msg_type == "progress":
            # Update transcription progress
            progress = message.get("progress", 0.0)
            self.update_episode_state(
                guid,
                EpisodeState.TRANSCRIBING,
                transcription_progress=progress
            )

        elif msg_type == "segment":
            # Add transcription segment
            text = message.get("text", "")
            self.add_transcription_segment(guid, text)

        elif msg_type == "complete":
            # Transcription complete
            text = message.get("text", "")
            segments = message.get("segments")
            # Save transcription with timestamps
            self.transcription_cache.add_transcription(guid, text, segments)
            self.transcription_cache.save()
            # Mark as complete
            self.update_episode_state(
                guid,
                EpisodeState.TRANSCRIBED
            )

        elif msg_type == "error":
            # Transcription error
            error = message.get("error", "Unknown error")
            self.update_episode_state(
                guid,
                EpisodeState.ERROR,
                error_message=error
            )
