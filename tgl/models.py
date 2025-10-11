"""Pydantic data models for TGL application

This module contains only data models. Configuration management
is handled in the config module.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class TrackInfo(BaseModel):
    """Track information model"""
    artist: str
    title: str
    variant: Optional[str] = None  # e.g., "Original Mix", "Fabrizio Mammarella Remix", "feat. JX"


class Episode(BaseModel):
    """Episode metadata model"""
    id: int
    episode_id: Optional[str] = None  # Formatted ID like "E101" or "X01"
    title: str
    full_title: str
    description: str  # Raw HTML description
    description_text: Optional[str] = None  # Clean text before tracklist
    tracklist: Optional[List[TrackInfo]] = None  # Structured tracklist
    published: str
    year: Optional[int] = None
    link: str
    audio_url: Optional[str] = None
    episode_type: str = 'TGL'  # 'TGL' or 'BONUS'
    duration: Optional[str] = None  # Episode duration (e.g., "1:23:45" or "45:30")

    def model_post_init(self, __context):
        """Set default episode_id if not provided (for backward compatibility)"""
        if self.episode_id is None:
            if self.episode_type == 'TGL':
                self.episode_id = f"E{self.id}"
            else:
                # BONUS episodes use id >= 10000, subtract offset to get B number
                b_number = self.id - 10000 if self.id >= 10000 else self.id
                self.episode_id = f"B{b_number:02d}"

    class Config:
        json_schema_extra = {
            "example": {
                "id": 390,
                "title": "Love Songs and Haunted Nights",
                "full_title": "TGL E390: Love Songs and Haunted Nights",
                "description": "Episode description...",
                "description_text": "This week's episode...",
                "tracklist": [{"artist": "Prospa", "title": "Love Songs"}],
                "published": "2024-10-15",
                "year": 2024,
                "link": "https://patreon.com/...",
                "audio_url": "https://..."
            }
        }


class Track(BaseModel):
    """Track model for Spotify operations"""
    artist: str
    track: str
    query: str


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
