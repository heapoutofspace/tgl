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


def transcribe_audio(audio_path: Path, model: str = "openai/whisper-large-v3") -> str:
    """Transcribe an audio file using insanely-fast-whisper

    Args:
        audio_path: Path to audio file
        model: Whisper model to use

    Returns:
        Transcription text

    Raises:
        RuntimeError: If transcription fails
    """
    import subprocess
    import tempfile
    import sys
    import platform

    # Detect available device
    device_id = "cpu"
    batch_size = 4  # Conservative default for CPU

    try:
        import torch
        if torch.cuda.is_available():
            device_id = "0"  # Use first CUDA GPU
            batch_size = 24  # Larger batch for GPU
            console.print(f"[dim]Using CUDA GPU for transcription[/dim]")
        elif platform.system() == "Darwin" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device_id = "mps"  # Use Apple Metal Performance Shaders
            batch_size = 4  # Smaller batch for Mac
            console.print(f"[dim]Using MPS (Apple Silicon) for transcription[/dim]")
        else:
            console.print(f"[dim]Using CPU for transcription (this will be slow)[/dim]")
    except ImportError:
        console.print(f"[yellow]Warning: PyTorch not found, using CPU[/yellow]")

    # Create a temporary file for the output
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        output_file = Path(tmp.name)

    try:
        # Run insanely-fast-whisper
        cmd = [
            "insanely-fast-whisper",
            "--file-name", str(audio_path),
            "--model-name", model,
            "--batch-size", str(batch_size),
            "--device-id", device_id,
            "--transcript-path", str(output_file),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        # Read the transcription from the JSON output
        with open(output_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Extract just the text
        if isinstance(data, dict) and 'text' in data:
            return data['text'].strip()
        elif isinstance(data, dict) and 'segments' in data:
            # Concatenate all segment texts
            segments = data['segments']
            text = ' '.join(seg.get('text', '') for seg in segments)
            return text.strip()
        else:
            raise RuntimeError(f"Unexpected transcription format: {data}")

    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Transcription failed: {e.stderr}")
    except Exception as e:
        raise RuntimeError(f"Transcription error: {e}")
    finally:
        # Clean up temporary file
        if output_file.exists():
            output_file.unlink()
