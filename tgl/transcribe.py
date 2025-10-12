"""Transcription management for TGL episodes"""

import json
from pathlib import Path
from typing import Optional, Dict
from rich.console import Console

from .config import paths

console = Console()


class TranscriptionCache:
    """Manages episode transcriptions"""

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize transcription cache

        Args:
            cache_dir: Optional directory for cache. Uses platform-specific dir by default
        """
        self.cache_dir = cache_dir if cache_dir else paths.data_dir
        self.transcriptions_file = self.cache_dir / "transcriptions.json"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.transcriptions: Dict[str, str] = {}  # guid -> text
        self._load()

    def _load(self):
        """Load transcriptions from cache file"""
        if self.transcriptions_file.exists():
            try:
                with open(self.transcriptions_file, 'r', encoding='utf-8') as f:
                    self.transcriptions = json.load(f)
                console.print(f"[dim]Loaded {len(self.transcriptions)} transcriptions from cache[/dim]")
            except (json.JSONDecodeError, IOError) as e:
                console.print(f"[yellow]Warning: Could not load transcriptions: {e}[/yellow]")
                self.transcriptions = {}

    def save(self):
        """Save transcriptions to cache file"""
        try:
            with open(self.transcriptions_file, 'w', encoding='utf-8') as f:
                json.dump(self.transcriptions, f, indent=2, ensure_ascii=False)
            console.print(f"[dim]Saved {len(self.transcriptions)} transcriptions to cache[/dim]")
        except IOError as e:
            console.print(f"[red]Error saving transcriptions: {e}[/red]")

    def has_transcription(self, guid: str) -> bool:
        """Check if a transcription exists for an episode

        Args:
            guid: Episode GUID

        Returns:
            True if transcription exists
        """
        return guid in self.transcriptions

    def get_transcription(self, guid: str) -> Optional[str]:
        """Get transcription for an episode

        Args:
            guid: Episode GUID

        Returns:
            Transcription text or None if not found
        """
        return self.transcriptions.get(guid)

    def add_transcription(self, guid: str, text: str):
        """Add or update a transcription

        Args:
            guid: Episode GUID
            text: Transcription text
        """
        self.transcriptions[guid] = text

    def get_all_transcriptions(self) -> Dict[str, str]:
        """Get all transcriptions

        Returns:
            Dictionary mapping GUIDs to transcription text
        """
        return self.transcriptions.copy()


def transcribe_audio(audio_path: Path, model_size: str = "large-v3", language: str = "en") -> str:
    """Transcribe an audio file using faster-whisper

    Args:
        audio_path: Path to audio file
        model_size: Whisper model size (e.g., "large-v3", "medium", "small")
        language: Language code (e.g., "en" for English)

    Returns:
        Transcription text

    Raises:
        RuntimeError: If transcription fails
    """
    try:
        from faster_whisper import WhisperModel
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
                console.print(f"[dim]Using CUDA GPU for transcription[/dim]")
            else:
                console.print(f"[dim]Using CPU for transcription (this will be slower)[/dim]")
        except ImportError:
            console.print(f"[dim]PyTorch not found, using CPU for transcription[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: Device detection failed, using CPU: {e}[/yellow]")

    try:
        # Initialize the model
        # Note: faster-whisper downloads models to cache automatically
        model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=None,  # Use default cache location
        )

        console.print(f"[dim]Model: {model_size}, Language: {language}, Device: {device}, Compute: {compute_type}[/dim]")

        # Transcribe the audio
        # beam_size=5 is a good balance between speed and accuracy
        # vad_filter=True enables voice activity detection to skip silence
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        # Collect all segment texts
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text)

        full_text = " ".join(text_parts).strip()

        if not full_text:
            raise RuntimeError("No text was transcribed from the audio")

        return full_text

    except Exception as e:
        raise RuntimeError(f"Transcription failed: {e}")
