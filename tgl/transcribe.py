"""Transcription management for TGL episodes"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Callable
from rich.console import Console

from .config import paths

console = Console()


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS timestamp

    Args:
        seconds: Time in seconds

    Returns:
        Formatted timestamp string
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes:02d}:{secs:02d}"


# Set environment variables to avoid multiprocessing issues in threads
# os.environ['OMP_NUM_THREADS'] = '1'
# os.environ['MKL_NUM_THREADS'] = '1'

# Configure multiprocessing for faster-whisper
# Must be set before importing torch or faster_whisper
try:
    import multiprocessing as mp
    # Use 'fork' instead of 'spawn' to avoid file descriptor issues with Textual
    # This must be done before any torch/faster-whisper operations
    if mp.get_start_method(allow_none=True) is None:
        mp.set_start_method('fork', force=True)
except Exception as e:
    console.print(f"[yellow]Warning: Could not set multiprocessing start method: {e}[/yellow]")


class TranscriptionCache:
    """Manages episode transcriptions with timestamps"""

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize transcription cache

        Args:
            cache_dir: Optional directory for cache. Uses platform-specific dir by default
        """
        self.cache_dir = cache_dir if cache_dir else paths.data_dir
        self.transcriptions_dir = self.cache_dir / "transcriptions"
        self.transcriptions_dir.mkdir(parents=True, exist_ok=True)

        # Count existing transcriptions
        transcription_count = len(list(self.transcriptions_dir.glob("*.json")))
        if transcription_count > 0:
            console.print(f"[dim]Loaded {transcription_count} transcriptions from cache[/dim]")

    def _get_transcription_file(self, guid: str) -> Path:
        """Get the file path for a transcription

        Args:
            guid: Episode GUID

        Returns:
            Path to the transcription JSON file
        """
        # Sanitize guid for filename (replace invalid characters)
        safe_guid = guid.replace('/', '_').replace('\\', '_')
        return self.transcriptions_dir / f"{safe_guid}.json"

    def save(self):
        """Save transcriptions (no-op, kept for compatibility with existing code)

        Individual transcriptions are saved immediately when added.
        """
        pass

    def has_transcription(self, guid: str) -> bool:
        """Check if a transcription exists for an episode

        Args:
            guid: Episode GUID

        Returns:
            True if transcription exists
        """
        return self._get_transcription_file(guid).exists()

    def get_transcription(self, guid: str) -> Optional[str]:
        """Get transcription text for an episode

        Args:
            guid: Episode GUID

        Returns:
            Transcription text or None if not found
        """
        transcription_file = self._get_transcription_file(guid)
        if not transcription_file.exists():
            return None

        try:
            with open(transcription_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('text')
        except (json.JSONDecodeError, IOError) as e:
            console.print(f"[yellow]Warning: Could not load transcription for {guid}: {e}[/yellow]")
            return None

    def get_transcription_segments(self, guid: str) -> Optional[list]:
        """Get transcription segments with timestamps for an episode

        Args:
            guid: Episode GUID

        Returns:
            List of segment dicts with start, end, text keys, or None if not found
        """
        transcription_file = self._get_transcription_file(guid)
        if not transcription_file.exists():
            return None

        try:
            with open(transcription_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('segments')
        except (json.JSONDecodeError, IOError) as e:
            console.print(f"[yellow]Warning: Could not load transcription for {guid}: {e}[/yellow]")
            return None

    def add_transcription(self, guid: str, text: str, segments: Optional[list] = None):
        """Add or update a transcription with optional timestamps

        Args:
            guid: Episode GUID
            text: Full transcription text
            segments: Optional list of segment dicts with start, end, text keys
        """
        transcription_file = self._get_transcription_file(guid)

        data = {
            'text': text,
            'segments': segments or []
        }

        try:
            with open(transcription_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            console.print(f"[red]Error saving transcription for {guid}: {e}[/red]")

    def get_all_transcriptions(self) -> Dict[str, str]:
        """Get all transcriptions as text only

        Returns:
            Dictionary mapping GUIDs to transcription text
        """
        result = {}

        # Iterate through all .json files in transcriptions directory
        for transcription_file in self.transcriptions_dir.glob("*.json"):
            # Extract guid from filename (remove .json extension)
            guid = transcription_file.stem

            try:
                with open(transcription_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                result[guid] = data.get('text', '')
            except (json.JSONDecodeError, IOError):
                # Skip files that can't be read
                continue

        return result


def transcribe_audio(
    audio_path: Path,
    model_size: str = "large-v3",
    language: str = "en",
    segment_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[float], None]] = None,
    shutdown_callback: Optional[Callable[[], bool]] = None,
    batch_size: Optional[int] = None
) -> tuple[str, list]:
    """Transcribe an audio file using faster-whisper

    Args:
        audio_path: Path to audio file
        model_size: Whisper model size (e.g., "large-v3", "medium", "small")
        language: Language code (e.g., "en" for English)
        segment_callback: Optional callback(segment_text) called for each segment
        progress_callback: Optional callback(progress_pct) called with progress percentage
        shutdown_callback: Optional callback() -> bool that returns True if shutdown requested
        batch_size: Optional batch size for BatchedInferencePipeline (faster processing)

    Returns:
        Tuple of (full_text, segments) where segments is a list of dicts with start, end, text keys

    Raises:
        RuntimeError: If transcription fails or shutdown requested
    """
    try:
        from faster_whisper import WhisperModel
        if batch_size:
            from faster_whisper import BatchedInferencePipeline
    except ImportError as e:
        raise RuntimeError(f"faster-whisper not installed: {e}. Please install faster-whisper>=1.0.0")

    # Detect available device and configure settings
    device = "cpu"
    compute_type = "int8"  # CPU default

    try:
        # Check for CUDA
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
                compute_type = "float16"
                if not segment_callback:  # Only print if not using TUI
                    console.print(f"[dim]Using CUDA GPU for transcription[/dim]")
            else:
                if not segment_callback:
                    console.print(f"[dim]Using CPU for transcription (this will be slower)[/dim]")
        except ImportError:
            if not segment_callback:
                console.print(f"[dim]PyTorch not found, using CPU for transcription[/dim]")
    except Exception as e:
        if not segment_callback:
            console.print(f"[yellow]Warning: Device detection failed, using CPU: {e}[/yellow]")

    try:
        # Initialize the model
        # Note: faster-whisper downloads models to cache automatically
        # Multiprocessing is configured at module import to use 'fork' method
        base_model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=None,  # Use default cache location
        )
        kwargs = {}

        # Use BatchedInferencePipeline for faster processing if batch_size is specified
        if batch_size:
            model = BatchedInferencePipeline(model=base_model)
            kwargs['batch_size'] = batch_size
            if not segment_callback:
                console.print(f"[dim]Model: {model_size}, Language: {language}, Device: {device}, Compute: {compute_type}, Batch: {batch_size}[/dim]")
        else:
            model = base_model
            if not segment_callback:
                console.print(f"[dim]Model: {model_size}, Language: {language}, Device: {device}, Compute: {compute_type}[/dim]")

        # Transcribe the audio
        # beam_size=5 is a good balance between speed and accuracy
        # vad_filter=True enables voice activity detection to skip silence
        # initial_prompt provides context to improve accuracy for proper nouns and domain terminology
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            initial_prompt="This is The Guestlist, a moderated music podcast by Fear of Tigers featuring electronic music tracks, artist names, and commentary.",
            **kwargs
        )

        # Collect all segment texts and timestamps with callbacks
        text_parts = []
        segment_data = []
        segment_count = 0

        # Get audio duration for progress calculation
        audio_duration = info.duration if hasattr(info, 'duration') else None

        for segment in segments:
            # Check for shutdown before processing each segment
            if shutdown_callback and shutdown_callback():
                raise RuntimeError("Transcription aborted due to shutdown")

            segment_text = segment.text.strip()
            text_parts.append(segment.text)
            segment_count += 1

            # Store segment with timestamps
            segment_data.append({
                'start': segment.start,
                'end': segment.end,
                'text': segment_text
            })

            # Call segment callback if provided
            if segment_callback:
                segment_callback(segment_text)

            # Calculate and report progress if we have duration info
            if progress_callback and audio_duration:
                # Use segment end time for progress
                progress_pct = (segment.end / audio_duration) * 100
                progress_callback(min(progress_pct, 99.0))  # Cap at 99% until complete

        full_text = " ".join(text_parts).strip()

        # Report 100% complete
        if progress_callback:
            progress_callback(100.0)

        if not full_text:
            raise RuntimeError("No text was transcribed from the audio")

        return full_text, segment_data

    except Exception as e:
        raise RuntimeError(f"Transcription failed: {e}")
