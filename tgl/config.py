"""Configuration management for TGL application

This module handles all configuration-related functionality:
- Platform-specific directory paths
- Settings loading from environment variables, config files, and .env
- Config file location override via TGL_CONFIG environment variable
"""

from pathlib import Path
from typing import Optional, Tuple
from platformdirs import user_data_dir, user_config_dir
from pydantic import BaseModel, Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource, TomlConfigSettingsSource


class PathOverrides(BaseSettings):
    """Minimal settings class for reading path overrides from env vars and .env

    This is loaded before the main Settings class to avoid circular dependencies.
    """
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore'
    )

    data_dir: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('TGL_DATA_DIR', 'DATA_DIR'),
        description="Override data directory location"
    )

    config_file: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('TGL_CONFIG'),
        description="Override config file location"
    )


class TGLPaths:
    """Centralized path management for TGL application

    Uses platformdirs to provide platform-specific directories:
    - macOS: ~/Library/Application Support/TGL
    - Linux: ~/.local/share/TGL and ~/.config/TGL
    - Windows: C:\\Users\\<user>\\AppData\\Local\\TGL

    Supports overrides:
    - TGL_DATA_DIR: Override data directory location
    - TGL_CONFIG: Override config file location
    """

    # Application name for platformdirs
    APP_NAME = "TGL"
    APP_AUTHOR = "TGL"

    def __init__(self):
        # Load path overrides using pydantic-settings (reads from env vars and .env)
        overrides = PathOverrides()

        # Data directory (persistent data, cache, state)
        if overrides.data_dir:
            self._data_dir = Path(overrides.data_dir).expanduser().resolve()
        else:
            self._data_dir = Path(user_data_dir(self.APP_NAME, self.APP_AUTHOR))

        # Config directory (configuration files)
        self._config_dir = Path(user_config_dir(self.APP_NAME, self.APP_AUTHOR))

        # Config file location
        if overrides.config_file:
            self._config_file = Path(overrides.config_file).expanduser().resolve()
        else:
            self._config_file = self._config_dir / "config.toml"

        # Create directories if they don't exist
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        """Main data directory for episodes cache, search index, and state"""
        return self._data_dir

    @property
    def config_dir(self) -> Path:
        """Configuration directory for config files (when not overridden)"""
        return self._config_dir

    @property
    def config_file(self) -> Path:
        """User configuration file (TOML format)"""
        return self._config_file

    @property
    def episodes_cache(self) -> Path:
        """Episode metadata cache file"""
        return self.data_dir / "episodes.json"

    @property
    def search_index_dir(self) -> Path:
        """Whoosh search index directory"""
        return self.data_dir / "search_index"

    @property
    def state_file(self) -> Path:
        """Production state file"""
        return self.data_dir / "state.json"

    @property
    def state_file_dryrun(self) -> Path:
        """Dryrun state file"""
        return self.data_dir / "state_dryrun.json"

    @property
    def spotify_cache(self) -> Path:
        """Spotify OAuth token cache (DEPRECATED - now stored in spotify.json)

        This property is kept for backward compatibility but is no longer used.
        OAuth tokens are now stored in the oauth_token field of spotify.json.
        """
        return self.data_dir / ".spotify_cache"

    def __repr__(self) -> str:
        return f"TGLPaths(data_dir={self.data_dir}, config_file={self.config_file})"


# Global paths instance (must be initialized before Settings)
paths = TGLPaths()


class Settings(BaseSettings):
    """Application settings loaded from environment variables and config file

    Priority order (highest to lowest):
    1. Environment variables (TGL_ prefixed versions)
    2. Environment variables (non-prefixed versions)
    3. TOML config file (TGL_CONFIG or platform-specific location)
    4. .env file (project directory)
    5. Default values

    Accepts both TGL_ prefixed and non-prefixed variable names for backward compatibility.
    Prefixed versions take priority if both exist.
    """
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        populate_by_name=True  # Allow field name in addition to aliases (needed for TOML config)
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
        3. TOML config file (from paths.config_file)
        4. .env file
        5. File secrets
        """
        toml_settings = None
        if paths.config_file.exists():
            toml_settings = TomlConfigSettingsSource(settings_cls, paths.config_file)

        # Return sources in priority order
        if toml_settings:
            return (init_settings, env_settings, toml_settings, dotenv_settings, file_secret_settings)
        else:
            return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    # Patreon RSS feed URL (required for most operations)
    patreon_rss_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('TGL_PATREON_RSS_URL', 'PATREON_RSS_URL'),
        description="Patreon RSS feed URL with auth token"
    )

    # Spotify API credentials (optional - only needed for spotify command)
    spotify_client_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('TGL_SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_ID'),
        description="Spotify API client ID"
    )

    spotify_client_secret: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('TGL_SPOTIFY_CLIENT_SECRET', 'SPOTIFY_CLIENT_SECRET'),
        description="Spotify API client secret"
    )

    spotify_redirect_uri: str = Field(
        default='http://127.0.0.1:8888/callback',
        validation_alias=AliasChoices('TGL_SPOTIFY_REDIRECT_URI', 'SPOTIFY_REDIRECT_URI'),
        description="Spotify OAuth redirect URI"
    )

    spotify_playlist_name: str = Field(
        default='The Sound of The Guestlist by Fear of Tigers',
        validation_alias=AliasChoices('TGL_SPOTIFY_PLAYLIST_NAME', 'SPOTIFY_PLAYLIST_NAME'),
        description="Default Spotify playlist name"
    )

    spotify_episode_playlist_format: str = Field(
        default='TGL {id}: {title}',
        validation_alias=AliasChoices('TGL_SPOTIFY_EPISODE_PLAYLIST_FORMAT', 'SPOTIFY_EPISODE_PLAYLIST_FORMAT'),
        description="Format for episode playlist names ({id} and {title} are replaced)"
    )

    # Path overrides (documentation only - these are read directly by TGLPaths before Settings loads)
    data_dir: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('TGL_DATA_DIR', 'DATA_DIR'),
        description="Override data directory location (env var only, not config file)"
    )

    config_file: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices('TGL_CONFIG'),
        description="Override config file location (env var only, not config file)"
    )


# Global settings instance
settings = Settings()
