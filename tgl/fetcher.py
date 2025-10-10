"""Patreon RSS feed fetcher and tracklist parser"""

import re
import time
from typing import List, Optional
from html import unescape
import requests
import feedparser
from rich.console import Console

from .models import Episode, TrackInfo, Track

console = Console()


class TracklistParser:
    """Parses tracklists from episode show notes"""

    def _strip_html(self, html_text: str) -> str:
        """Strip HTML tags and unescape HTML entities"""
        text = re.sub(r'<[^>]+>', '\n', html_text)
        text = unescape(text)
        return text

    def parse_tracklist(self, description: str) -> List[Track]:
        """Extract tracks from episode description"""
        clean_text = self._strip_html(description)

        tracks = []
        seen = set()

        for line in clean_text.split('\n'):
            line = line.strip()

            if not line or len(line) < 5:
                continue

            if any(marker in line.lower() for marker in ['tracklist', 'record of the week', 'from the crates', 'also recommended', 'guestmix']):
                continue

            line = re.sub(r'^[#\d\.\)]+\s*', '', line).strip()

            match = re.match(r'^(.+?)\s*[-–—]\s*(.+?)(?:\s*\([^\)]*\))?\s*$', line)

            if match:
                artist = match.group(1).strip()
                track_name = match.group(2).strip()

                track_name = re.sub(r'\s*\(Original Mix\)\s*$', '', track_name, flags=re.IGNORECASE)

                if len(artist) < 2 or len(track_name) < 2:
                    continue

                if 'http' in line.lower() or 'www.' in line.lower():
                    continue

                track_key = f"{artist.lower()}|{track_name.lower()}"
                if track_key not in seen:
                    seen.add(track_key)
                    tracks.append(Track(
                        artist=artist,
                        track=track_name,
                        query=f"{artist} {track_name}"
                    ))

        return tracks


class PatreonPodcastFetcher:
    """Fetches podcast episodes from Patreon RSS feed"""

    def __init__(self, rss_url: str):
        self.rss_url = rss_url
        self.parser = TracklistParser()

    def parse_episode_id(self, title: str) -> Optional[int]:
        """Parse episode ID from various title formats"""
        # Try different patterns in order of specificity
        patterns = [
            r'\bE\s*(\d+)\b',                          # TGL E390, TGL E 390
            r'TGL\s*-?\s*(\d+)\b',                     # TGL 382, TGL - 382, TGL-382
            r'(?:The\s+)?Guestlist\s*-?\s*Episode\s+(\d+)',  # The Guestlist - Episode 300, The Guestlist Episode 1
            r'(?:The\s+)?G-?list\s*-?\s*Episode?\s*(\d+)',   # G-list 95, Guestlist - Episode 299
        ]

        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return int(match.group(1))

        return None

    def _extract_description_text(self, html_description: str) -> str:
        """Extract clean text before the tracklist begins"""
        clean_text = self.parser._strip_html(html_description)

        # Find where the tracklist section begins
        tracklist_markers = [
            'tracklist',
            'track list',
            'tracks:',
        ]

        lines = clean_text.split('\n')
        description_lines = []
        found_tracklist = False

        for line in lines:
            stripped = line.strip()
            line_lower = stripped.lower()

            # Check if this line marks the start of the tracklist
            if any(marker in line_lower for marker in tracklist_markers):
                found_tracklist = True
                break

            # Check if this line looks like a track entry (starts with # followed by Artist - Track)
            # More strict: must start with # or digit
            if re.match(r'^[#\d\.\)]+\s+.+?\s*[-–—]\s*.+', stripped):
                found_tracklist = True
                break

            # Don't break on regular sentences that happen to have dashes
            if stripped:
                description_lines.append(stripped)

        return '\n'.join(description_lines).strip()

    def _parse_structured_tracklist(self, html_description: str) -> List[TrackInfo]:
        """Parse tracklist into structured format"""
        clean_text = self.parser._strip_html(html_description)
        lines = clean_text.split('\n')
        tracks = []
        seen = set()
        in_tracklist = False

        # First pass: look for explicit tracklist markers or detect implicit tracklist
        # by finding multiple consecutive lines with "Artist - Track" format
        for i, line in enumerate(lines):
            line = line.strip()
            line_lower = line.lower()

            if any(marker in line_lower for marker in ['tracklist', 'track list', 'tracks:']):
                in_tracklist = True
                break

        # Second pass: try to detect implicit tracklist (multiple Artist - Track lines in a row)
        if not in_tracklist:
            consecutive_track_lines = 0
            # Weak prose indicators - common words that can appear in artist names (and, the, with, from, for)
            weak_prose_indicators = [
                r'\b(if|this|that|you|your|we|our|my|me|be|have|has|had|will|would|could|should|can|may|might|must|shall|do|does|did|is|am|are|was|were|been|being|happy|thanks|today|week|year|episode|podcast)\b'
            ]
            # Strong prose indicators - always filter these (contractions, very conversational words)
            strong_prose_indicators = [
                r"(?:n't|'ll|'ve|'re)\b",  # Contractions
                r"\b[a-z]+'s\b",  # Contraction 's after lowercase word (e.g., "it's", "that's", "week's") - \b ensures word boundary
                r'\b(channeling|expect)\b'  # Very conversational words
            ]

            for i, line in enumerate(lines):
                line = line.strip()

                # Skip blank/short lines but don't reset counter (they might be between tracks)
                if len(line) < 5:
                    continue

                # Skip very long lines (likely prose) and reset counter
                if len(line) > 100:
                    consecutive_track_lines = 0
                    continue

                # Check if line looks like "Artist - Track"
                cleaned = re.sub(r'^(?:RECORD OF THE WEEK|ROTW|FROM THE CRATES|FROM THE BLOGS|TRACK OF THE WEEK|ALSO RECOMMENDED):\s*', '', line, flags=re.IGNORECASE).strip()
                cleaned = re.sub(r'^[#\d\.\)]+\s*', '', cleaned).strip()

                match = re.match(r'^(.+?)\s*[-–—]\s*(.+)', cleaned)
                if match and not any(skip in line.lower() for skip in ['http', 'www.', 'patreon']):
                    # Check if artist part looks like prose
                    artist_part_original = match.group(1)
                    artist_part_lower = artist_part_original.lower()

                    # Strong prose indicators always cause skip
                    # Check contractions against original to distinguish possessives (Phantom's) from contractions (it's)
                    is_strong_prose = any(re.search(pattern, artist_part_original) for pattern in strong_prose_indicators)
                    if is_strong_prose:
                        consecutive_track_lines = 0
                        continue

                    # Check if it has proper artist name structure (capitalized words)
                    # If artist part has multiple capital letters, likely a proper name
                    has_capitals = sum(1 for c in artist_part_original if c.isupper()) >= 2

                    is_weak_prose = any(re.search(pattern, artist_part_lower) for pattern in weak_prose_indicators)

                    # Allow lines with weak prose words if they have proper capitalization (artist names)
                    if not is_weak_prose or has_capitals:
                        consecutive_track_lines += 1
                        if consecutive_track_lines >= 3:
                            # Found at least 3 consecutive track-like lines, assume it's a tracklist
                            in_tracklist = True
                            break
                    else:
                        consecutive_track_lines = 0
                else:
                    # Line doesn't match track pattern - reset counter
                    consecutive_track_lines = 0

        # Third pass: parse tracks
        tracklist_found = False
        for line in lines:
            line = line.strip()

            if not line or len(line) < 5:
                continue

            line_lower = line.lower()

            # Check if we've entered the tracklist section
            if any(marker in line_lower for marker in ['tracklist', 'track list', 'tracks:']):
                in_tracklist = True
                tracklist_found = True
                continue

            # If explicit tracklist marker was found, only parse lines after it
            if tracklist_found and not in_tracklist:
                continue

            # Check if we've left the tracklist section (common section separators)
            # Use more specific patterns to avoid false positives with artist names
            exit_patterns = [
                r'^-{3,}',  # Multiple dashes at start
                r'^={3,}',  # Multiple equals at start
                r'\bbest of\b',
                r'\blonglist\b',
                r'\blinks:\b',
                r'\bsupport us\b',  # More specific than just "support"
                r'\bpatreon\b',
                r'\bthanks to\b',
                r'\bour love to\b'
            ]
            if in_tracklist and any(re.search(pattern, line_lower) for pattern in exit_patterns):
                in_tracklist = False
                continue

            # Handle special prefixes
            if line_lower.startswith('record of the week:') or line_lower.startswith('from the crates:') or line_lower.startswith('from the blogs:') or line_lower.startswith('track of the week:') or line_lower.startswith('also recommended:'):
                # Extract the track from after the prefix
                prefix_match = re.match(r'^(?:RECORD OF THE WEEK|ROTW|FROM THE CRATES|FROM THE BLOGS|TRACK OF THE WEEK|ALSO RECOMMENDED):\s*(.+)', line, flags=re.IGNORECASE)
                if prefix_match:
                    line = prefix_match.group(1)

            # Check if line has a track marker
            has_track_marker = re.match(r'^[#\d\.\)]+\s', line)

            # Parse if: has marker OR in_tracklist section
            cleaned_line = re.sub(r'^[#\d\.\)]+\s*', '', line).strip()

            if not has_track_marker and not in_tracklist:
                continue

            # Try to parse "Artist - Track (Variant)" format
            # Capture variant info from parentheses or brackets at the end
            match = re.match(r'^(.+?)\s*[-–—]\s*(.+?)(?:\s*[\(\[]([^\)\]]+)[\)\]])?\s*$', cleaned_line)

            if match:
                artist = match.group(1).strip()
                track_title = match.group(2).strip()
                variant = match.group(3).strip() if match.group(3) else None

                # Clean up common prefixes from artist name
                artist = re.sub(r'^(?:RECORD OF THE WEEK|ROTW|FROM THE CRATES|FROM THE BLOGS|TRACK OF THE WEEK):\s*', '', artist, flags=re.IGNORECASE).strip()

                # If "Original Mix" is the variant, set to None (default)
                if variant and 'original mix' in variant.lower():
                    variant = None

                # Skip if too short or too long (likely not a track)
                if len(artist) < 2 or len(track_title) < 2:
                    continue

                if len(artist) > 100 or len(track_title) > 150:
                    continue

                # Skip URLs
                if 'http' in line.lower() or 'www.' in line.lower():
                    continue

                # Skip prose (check artist part for prose indicators)
                if not has_track_marker:
                    # Weak prose indicators - common words that can appear in artist names
                    weak_prose_indicators = [
                        r'\b(if|this|that|you|your|we|our|my|me|be|have|has|had|will|would|could|should|can|may|might|must|shall|do|does|did|is|am|are|was|were|been|being|happy|thanks|today|week|year|episode|podcast)\b'
                    ]
                    # Strong prose indicators - always filter these (contractions, very conversational words)
                    strong_prose_indicators = [
                        r"(?:n't|'ll|'ve|'re)\b",  # Contractions
                        r"\b[a-z]+'s\b",  # Contraction 's after lowercase word (e.g., "it's", "that's", "week's") - \b ensures word boundary
                        r'\b(channeling|expect)\b'  # Very conversational words
                    ]

                    # Strong prose indicators always cause skip
                    # Check contractions against original to distinguish possessives (Phantom's) from contractions (it's)
                    is_strong_prose = any(re.search(pattern, artist) for pattern in strong_prose_indicators)
                    if is_strong_prose:
                        continue

                    # Check if it has proper artist name structure (capitalized words)
                    has_capitals = sum(1 for c in artist if c.isupper()) >= 2

                    is_weak_prose = any(re.search(pattern, artist.lower()) for pattern in weak_prose_indicators)

                    # Only skip if it's weak prose AND doesn't have proper capitalization
                    if is_weak_prose and not has_capitals:
                        continue

                # Avoid duplicates
                track_key = f"{artist.lower()}|{track_title.lower()}"
                if track_key not in seen:
                    seen.add(track_key)
                    tracks.append(TrackInfo(
                        artist=artist,
                        title=track_title,
                        variant=variant
                    ))

        return tracks

    def classify_episode_type(self, title: str) -> str:
        """Classify episode as TGL or BONUS based on title"""
        title_lower = title.lower()

        # BONUS episode patterns (specific bonus content)
        bonus_patterns = [
            r'\bfrom the crates\b',
            r'\bfrom the blogs\b',
            r'\bfear of tigers\b',
            r'\bre-?up\b',
            r'\btrailer\b',
            r'\binterview\b',
            r'\bextra\b',  # "The Guestlist Extra" etc.
            r'\blistening guide\b',  # "Echo Drop EP and listening guide" etc.
        ]

        for pattern in bonus_patterns:
            if re.search(pattern, title_lower):
                return 'BONUS'

        # TGL episode patterns
        tgl_patterns = [
            r'\btgl\b',
            r'\bguestlist\b',
            r'\bg-?list\b',  # Catches both "guestlist" and "g-list"
            r'\bepisode\s+\d+',
            r'\be[\s:]*\d+',
        ]

        for pattern in tgl_patterns:
            if re.search(pattern, title_lower):
                return 'TGL'

        # Default to TGL for ambiguous cases (most episodes are TGL)
        return 'TGL'

    def assign_episode_id(self, title: str, episode_type: str, bonus_counter: int) -> str:
        """Assign episode ID based on type

        Args:
            title: Episode title
            episode_type: 'TGL' or 'BONUS'
            bonus_counter: Counter for BONUS episodes

        Returns:
            Episode ID (E{num} for TGL, B{num} for BONUS)
        """
        if episode_type == 'TGL':
            # Try to parse numeric ID from title
            numeric_id = self.parse_episode_id(title)
            if numeric_id:
                return f"E{numeric_id}"
            else:
                # Couldn't parse ID, use E???
                return "E???"
        else:
            # BONUS episodes get B prefix
            return f"B{bonus_counter:02d}"

    def fetch_episodes(self, limit: Optional[int] = None) -> List[Episode]:
        """Fetch episodes from the RSS feed"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; TGL-CLI/1.0)'
            }
            response = requests.get(self.rss_url, headers=headers, timeout=30)
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if feed.bozo:
                console.print(f"[yellow]Warning: Feed parsing encountered an issue: {feed.bozo_exception}[/yellow]")

            # First pass: collect all episodes
            temp_episodes = []
            entries_to_process = feed.entries if limit is None else feed.entries[:limit]

            for entry in entries_to_process:
                title = entry.get('title', '')

                # Classify episode type
                episode_type = self.classify_episode_type(title)

                # Extract clean title (after colon)
                clean_title = title
                if ':' in title:
                    clean_title = title.split(':', 1)[1].strip()

                # Parse published date
                published_parsed = entry.get('published_parsed')
                year = None
                published = entry.get('published', '')
                if published_parsed:
                    year = published_parsed.tm_year
                    published = time.strftime('%Y-%m-%d', published_parsed)

                # Get audio URL
                audio_url = None
                if hasattr(entry, 'enclosures') and entry.enclosures:
                    audio_url = entry.enclosures[0].get('href')

                # Get full description
                raw_description = entry.get('description', '') or entry.get('summary', '')

                # Parse description text and tracklist
                description_text = self._extract_description_text(raw_description)
                tracklist = self._parse_structured_tracklist(raw_description)

                # Store episode data temporarily
                temp_episodes.append({
                    'title': title,
                    'clean_title': clean_title,
                    'episode_type': episode_type,
                    'published': published,
                    'published_parsed': published_parsed,
                    'year': year,
                    'link': entry.get('link', ''),
                    'audio_url': audio_url,
                    'description': raw_description,
                    'description_text': description_text,
                    'tracklist': tracklist
                })

            # Second pass: assign IDs to BONUS episodes sequentially
            # Sort BONUS episodes by published date (oldest first)
            bonus_episodes = [ep for ep in temp_episodes if ep['episode_type'] == 'BONUS']
            bonus_episodes.sort(key=lambda ep: ep['published_parsed'] if ep['published_parsed'] else time.struct_time((1970, 1, 1, 0, 0, 0, 0, 1, 0)))

            # Create mapping from link to B number
            bonus_id_map = {}
            for idx, ep in enumerate(bonus_episodes, start=1):
                bonus_id_map[ep['link']] = idx

            # Third pass: create Episode objects with proper IDs
            episodes = []
            for ep_data in temp_episodes:
                episode_type = ep_data['episode_type']

                if episode_type == 'TGL':
                    numeric_id = self.parse_episode_id(ep_data['title'])
                    if numeric_id is None:
                        numeric_id = 0  # Fallback for unparseable TGL episodes
                    episode_id_str = f"E{numeric_id}" if numeric_id > 0 else "E???"
                else:
                    # BONUS episodes use sequential numbering with offset to avoid conflicts
                    # Use 10000 + sequence number as the internal numeric ID
                    b_number = bonus_id_map[ep_data['link']]
                    numeric_id = 10000 + b_number
                    episode_id_str = f"B{b_number:02d}"

                # Build normalized full_title with formatted ID
                if episode_type == 'TGL':
                    full_title = f"TGL {episode_id_str}: {ep_data['clean_title']}"
                else:
                    full_title = f"BONUS {episode_id_str}: {ep_data['clean_title']}"

                episode = Episode(
                    id=numeric_id,
                    episode_id=episode_id_str,
                    title=ep_data['clean_title'],
                    full_title=full_title,
                    description=ep_data['description'],
                    description_text=ep_data['description_text'],
                    tracklist=ep_data['tracklist'] if ep_data['tracklist'] else None,
                    published=ep_data['published'],
                    year=ep_data['year'],
                    link=ep_data['link'],
                    audio_url=ep_data['audio_url'],
                    episode_type=episode_type
                )
                episodes.append(episode)

            return episodes

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error fetching RSS feed: {e}[/red]")
            return []
