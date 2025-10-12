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
    """Transcribe an audio file using insanely-fast-whisper Python API

    Args:
        audio_path: Path to audio file
        model: Whisper model to use

    Returns:
        Transcription text

    Raises:
        RuntimeError: If transcription fails
    """
    import platform

    try:
        import torch
        from transformers import pipeline
        from transformers.utils import is_flash_attn_2_available
    except ImportError as e:
        raise RuntimeError(f"Required packages not installed: {e}. Please install transformers, optimum, and accelerate.")

    # Detect available device and configure settings
    device = "cpu"
    torch_dtype = torch.float32  # CPU uses float32
    batch_size = 4  # Conservative default

    try:
        if torch.cuda.is_available():
            device = "cuda:0"
            torch_dtype = torch.float16  # GPU can use float16
            batch_size = 24
            console.print(f"[dim]Using CUDA GPU for transcription[/dim]")
        elif platform.system() == "Darwin" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            device = "mps"
            torch_dtype = torch.float16
            batch_size = 4  # Smaller batch for Mac
            console.print(f"[dim]Using MPS (Apple Silicon) for transcription[/dim]")
        else:
            console.print(f"[dim]Using CPU for transcription (this will be slow)[/dim]")
    except Exception as e:
        console.print(f"[yellow]Warning: Device detection failed, using CPU: {e}[/yellow]")

    try:
        # Configure model kwargs based on flash attention availability
        model_kwargs = {}
        if is_flash_attn_2_available() and device.startswith("cuda"):
            model_kwargs["attn_implementation"] = "flash_attention_2"
            console.print(f"[dim]Using Flash Attention 2 for better performance[/dim]")
        else:
            model_kwargs["attn_implementation"] = "sdpa"

        # Create the pipeline
        pipe = pipeline(
            "automatic-speech-recognition",
            model=model,
            torch_dtype=torch_dtype,
            device=device,
            model_kwargs=model_kwargs,
        )

        # Transcribe the audio
        outputs = pipe(
            str(audio_path),
            chunk_length_s=30,
            batch_size=batch_size,
            return_timestamps=False,  # We only need text
        )

        # Extract text from outputs
        if isinstance(outputs, dict) and 'text' in outputs:
            return outputs['text'].strip()
        elif isinstance(outputs, str):
            return outputs.strip()
        else:
            raise RuntimeError(f"Unexpected output format: {type(outputs)}")

    except Exception as e:
        raise RuntimeError(f"Transcription failed: {e}")
