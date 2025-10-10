"""Platform-specific directory paths for TGL application"""

from pathlib import Path
from platformdirs import user_data_dir, user_config_dir


class TGLPaths:
    """Centralized path management for TGL application

    Uses platformdirs to provide platform-specific directories:
    - macOS: ~/Library/Application Support/TGL
    - Linux: ~/.local/share/TGL and ~/.config/TGL
    - Windows: C:\\Users\\<user>\\AppData\\Local\\TGL
    """

    # Application name for platformdirs
    APP_NAME = "TGL"
    APP_AUTHOR = "TGL"

    def __init__(self):
        # Data directory (persistent data, cache, state)
        self._data_dir = Path(user_data_dir(self.APP_NAME, self.APP_AUTHOR))

        # Config directory (configuration files)
        self._config_dir = Path(user_config_dir(self.APP_NAME, self.APP_AUTHOR))

        # Create directories if they don't exist
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._config_dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        """Main data directory for episodes cache, search index, and state"""
        return self._data_dir

    @property
    def config_dir(self) -> Path:
        """Configuration directory for config files"""
        return self._config_dir

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
        """Spotify OAuth token cache"""
        return self.data_dir / ".spotify_cache"

    @property
    def config_file(self) -> Path:
        """User configuration file (TOML format)"""
        return self.config_dir / "config.toml"

    def __repr__(self) -> str:
        return f"TGLPaths(data_dir={self.data_dir}, config_dir={self.config_dir})"


# Global paths instance
paths = TGLPaths()
