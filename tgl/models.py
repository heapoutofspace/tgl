"""Pydantic models for TGL application"""

from typing import List, Optional, Tuple
from pathlib import Path
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource, TomlConfigSettingsSource
from pydantic import AliasChoices


class Settings(BaseSettings):
    """Application settings loaded from environment variables and config file

    Priority order (highest to lowest):
    1. Environment variables (TGL_ prefixed versions)
    2. Environment variables (non-prefixed versions)
    3. TOML config file (platform-specific location)
    4. .env file (project directory)
    5. Default values

    Accepts both TGL_ prefixed and non-prefixed variable names for backward compatibility.
    Prefixed versions take priority if both exist.
    """
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        toml_file=None,  # Will be set dynamically
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources to add TOML config file support

        Priority (highest to lowest):
        1. Init settings (constructor arguments)
        2. Environment variables
        3. TOML config file
        4. .env file
        5. File secrets
        """
        # Import here to avoid circular dependency
        from .paths import paths

        toml_settings = None
        if paths.config_file.exists():
            toml_settings = TomlConfigSettingsSource(settings_cls, paths.config_file)

        # Return sources in priority order
        if toml_settings:
            return (init_settings, env_settings, toml_settings, dotenv_settings, file_secret_settings)
        else:
            return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    # Patreon RSS feed URL
    # Accepts: TGL_PATREON_RSS_URL or PATREON_RSS_URL
    patreon_rss_url: str = Field(
        ...,
        validation_alias=AliasChoices('TGL_PATREON_RSS_URL', 'PATREON_RSS_URL'),
        description="Patreon RSS feed URL with auth token"
    )

    # Spotify API credentials
    # Accepts: TGL_SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_ID
    spotify_client_id: str = Field(
        ...,
        validation_alias=AliasChoices('TGL_SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_ID'),
        description="Spotify API client ID"
    )

    # Accepts: TGL_SPOTIFY_CLIENT_SECRET or SPOTIFY_CLIENT_SECRET
    spotify_client_secret: str = Field(
        ...,
        validation_alias=AliasChoices('TGL_SPOTIFY_CLIENT_SECRET', 'SPOTIFY_CLIENT_SECRET'),
        description="Spotify API client secret"
    )

    # Accepts: TGL_SPOTIFY_REDIRECT_URI or SPOTIFY_REDIRECT_URI
    spotify_redirect_uri: str = Field(
        default='http://127.0.0.1:8888/callback',
        validation_alias=AliasChoices('TGL_SPOTIFY_REDIRECT_URI', 'SPOTIFY_REDIRECT_URI'),
        description="Spotify OAuth redirect URI"
    )

    # Accepts: TGL_SPOTIFY_PLAYLIST_NAME or SPOTIFY_PLAYLIST_NAME
    spotify_playlist_name: str = Field(
        default='TGL',
        validation_alias=AliasChoices('TGL_SPOTIFY_PLAYLIST_NAME', 'SPOTIFY_PLAYLIST_NAME'),
        description="Default Spotify playlist name"
    )

    # Data directory override (environment variable only, not config file)
    # Note: This must be set via environment variable or .env file before app starts
    # It cannot be set in config file since paths are initialized before config loads
    data_dir: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('TGL_DATA_DIR', 'DATA_DIR'),
        description="Override data directory location (env var only)"
    )


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
