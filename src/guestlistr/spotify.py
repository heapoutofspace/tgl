"""Spotify playlist management"""

from typing import List, Optional
import spotipy
from spotipy.oauth2 import SpotifyOAuth

from .models import Settings, Track


class SpotifyPlaylistManager:
    """Manages Spotify playlist creation and track additions"""

    def __init__(self, settings: Settings):
        scope = "playlist-modify-public playlist-modify-private"
        self.sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            redirect_uri=settings.spotify_redirect_uri,
            scope=scope
        ))
        self.user_id = self.sp.current_user()['id']

    def search_track(self, track: Track) -> Optional[str]:
        """Search for a track on Spotify and return the URI"""
        try:
            results = self.sp.search(q=track.query, type='track', limit=1)
            if results['tracks']['items']:
                return results['tracks']['items'][0]['uri']
            return None
        except Exception as e:
            return None

    def create_playlist(self, name: str, description: str = "") -> str:
        """Create a new playlist and return its ID"""
        playlist = self.sp.user_playlist_create(
            user=self.user_id,
            name=name,
            public=True,
            description=description
        )
        return playlist['id']

    def get_playlist_by_name(self, name: str) -> Optional[str]:
        """Find a playlist by name and return its ID"""
        playlists = self.sp.current_user_playlists()
        for playlist in playlists['items']:
            if playlist['name'] == name:
                return playlist['id']
        return None

    def add_tracks_to_playlist(self, playlist_id: str, track_uris: List[str]) -> int:
        """Add tracks to a playlist (in batches of 100)"""
        seen = set()
        unique_uris = []
        for uri in track_uris:
            if uri not in seen:
                seen.add(uri)
                unique_uris.append(uri)

        batch_size = 100
        for i in range(0, len(unique_uris), batch_size):
            batch = unique_uris[i:i + batch_size]
            self.sp.playlist_add_items(playlist_id, batch)

        return len(unique_uris)

    def get_playlist_tracks(self, playlist_id: str) -> List[str]:
        """Get all track URIs currently in a playlist"""
        tracks = []
        results = self.sp.playlist_items(playlist_id)

        while results:
            for item in results['items']:
                if item['track']:
                    tracks.append(item['track']['uri'])

            if results['next']:
                results = self.sp.next(results)
            else:
                break

        return tracks
