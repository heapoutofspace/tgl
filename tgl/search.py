"""Search index manager using Whoosh"""

import shutil
from typing import Dict, List, Optional
from pathlib import Path
from whoosh import index
from whoosh.fields import Schema, ID, TEXT, STORED
from whoosh.qparser import MultifieldParser, OrGroup

from .models import Episode
from .paths import paths


class SearchIndex:
    """Manages a Whoosh-based search index for episodes"""

    def __init__(self, cache_dir: Optional[Path] = None):
        # Use platform-specific data directory by default
        self.cache_dir = cache_dir if cache_dir else paths.data_dir
        self.index_dir = paths.search_index_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Define schema for episode search
        self.schema = Schema(
            episode_id=ID(stored=True),
            episode_id_str=STORED(),
            title=TEXT(stored=True, field_boost=3.0),
            description=TEXT(field_boost=1.0),
            artists=TEXT(field_boost=5.0),
            track_titles=TEXT(field_boost=2.0),
            episode_type=STORED()
        )

        # Create or open index
        if index.exists_in(str(self.index_dir)):
            self.ix = index.open_dir(str(self.index_dir))
        else:
            self.ix = index.create_in(str(self.index_dir), self.schema)

    def build_index(self, episodes: Dict[int, Episode]):
        """Build search index from episodes using Whoosh"""
        # Clear existing index by recreating it
        if self.index_dir.exists():
            shutil.rmtree(self.index_dir)
        self.index_dir.mkdir(exist_ok=True)
        self.ix = index.create_in(str(self.index_dir), self.schema)

        # Get a writer and add all documents
        writer = self.ix.writer()

        try:
            for ep_id, episode in episodes.items():
                # Collect all track artists and titles
                artists = []
                track_titles = []
                if episode.tracklist:
                    for track in episode.tracklist:
                        artists.append(track.artist)
                        track_titles.append(track.title)

                # Add document to index
                writer.add_document(
                    episode_id=str(ep_id),
                    episode_id_str=episode.episode_id or f"E{ep_id}",
                    title=episode.title,
                    description=episode.description_text or "",
                    artists=" ".join(artists),
                    track_titles=" ".join(track_titles),
                    episode_type=episode.episode_type
                )

            writer.commit()
        except Exception as e:
            writer.cancel()
            raise e

    def search(self, query: str, episodes: Dict[int, Episode]) -> List[Dict]:
        """Search episodes using Whoosh

        Returns list of matches with relevance scores.
        """
        # Create multifield parser
        parser = MultifieldParser(
            ["title", "description", "artists", "track_titles"],
            schema=self.schema,
            group=OrGroup
        )

        # Parse query
        q = parser.parse(query)

        results = []
        with self.ix.searcher() as searcher:
            search_results = searcher.search(q, limit=100, terms=True)

            for hit in search_results:
                ep_id = int(hit['episode_id'])
                episode = episodes.get(ep_id)

                if not episode:
                    continue

                # Determine match context based on which fields matched
                match_context = "Match found"
                matched_terms = hit.matched_terms()

                # Check which field had the match
                field_names = set(field for field, term in matched_terms)

                # Extract the actual matched terms for better context (decode bytes to strings)
                query_terms = set(term.decode('utf-8') if isinstance(term, bytes) else term for field, term in matched_terms)

                if 'artists' in field_names:
                    # Find which artist matched
                    if episode.tracklist:
                        for track in episode.tracklist:
                            artist_lower = track.artist.lower()
                            # Check if any query term matches this artist
                            if any(term in artist_lower for term in query_terms):
                                match_context = f"Track Artist: {track.artist}"
                                break
                elif 'track_titles' in field_names:
                    # Find which track matched
                    if episode.tracklist:
                        for track in episode.tracklist:
                            title_lower = track.title.lower()
                            if any(term in title_lower for term in query_terms):
                                match_context = f"Track: {track.artist} - {track.title}"
                                break
                elif 'title' in field_names:
                    match_context = f"Title: {episode.title}"
                elif 'description' in field_names:
                    match_context = "Description match"

                results.append({
                    'episode': episode,
                    'score': hit.score,
                    'context': match_context
                })

        return results
