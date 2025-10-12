"""Patreon RSS feed fetcher and tracklist parser"""

import re
import time
from typing import List, Optional
from html import unescape
from io import BytesIO
import requests
import feedparser
from rich.console import Console

from .models import Episode, TrackInfo, Track

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3NoHeaderError
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

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
        """Parse episode ID from various title formats

        Prioritizes explicit episode numbers in title text over prefix patterns
        """
        # First priority: explicit "Episode ###" patterns in title text
        explicit_patterns = [
            r'(?:The\s+)?Gue[^-\s]*list\s*[:-]?\s*Episode\s+(\d+)',  # The Guestlist - Episode 300, The Guestlist: Episode 140
            r'(?:The\s+)?G-?list\s*[:-]?\s*Episode\s+(\d+)',    # G-list - Episode 95, Guestlist: Episode 299
            r'TGL\s+Episode\s+(\d+)',                  # TGL Episode 208
            r'^Episode\s+(\d+)',                       # Episode 300 (at start of title)
        ]

        for pattern in explicit_patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return int(match.group(1))

        # Second priority: standard patterns
        standard_patterns = [
            r'\bE\s*(\d+)\b',                          # TGL E390, TGL E 390
            r'TGL\s*-?\s*(\d+)\b',                     # TGL 382, TGL - 382, TGL-382
            r'(?:The\s+)?Guestlist\s*-\s*(\d+)',       # The Guestlist - 170, Guestlist - 170
            r'(?:The\s+)?Guestlist\s+(\d+)',           # The Guestlist 47, The Guestlist 101
            r'G-?list\s+(\d+)',                        # G-list 95, Glist 82
        ]

        for pattern in standard_patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return int(match.group(1))

        return None

    def _clean_episode_title(self, title: str) -> str:
        """Clean episode title by removing episode numbers and podcast name

        Args:
            title: Episode title to clean

        Returns:
            Cleaned title (may be empty string if nothing remains)
        """
        cleaned = title

        # Remove episode number prefixes at the start
        # Pattern: E### - title, E### title, Episode ### - title, etc.
        cleaned = re.sub(r'^E\s*\d+\s*[-–—]?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'^Episode\s+\d+\s*[-–—]?\s*', '', cleaned, flags=re.IGNORECASE)

        # Remove "TGL" with optional episode number
        # Pattern: TGL E204 - title, TGL - title, TGL title
        cleaned = re.sub(r'^TGL\s+E?\s*\d*\s*[-–—]?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'^TGL\s*[-–—]?\s*', '', cleaned, flags=re.IGNORECASE)

        # Remove "The Guestlist" with optional episode number
        # Pattern: The Guestlist - Episode 24, The Guestlist Episode 1, The Guestlist - title
        cleaned = re.sub(r'^(?:The\s+)?Gue[^-\s]*list\s*[-–—]?\s*Episode\s+\d+\s*[-–—]?\s*', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'^(?:The\s+)?Gue[^-\s]*list\s*[-–—]?\s*', '', cleaned, flags=re.IGNORECASE)

        # Remove "G-list" variants
        cleaned = re.sub(r'^G-?list\s*[-–—]?\s*', '', cleaned, flags=re.IGNORECASE)

        # Strip any remaining leading/trailing whitespace and dashes
        cleaned = cleaned.strip(' \t\n\r-–—')

        return cleaned

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

                # Skip tracks with excessive character count (likely prose)
                # Check character count excluding the variant which is already separated
                if len(artist) > 70 or len(track_title) > 70:
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

    def _infer_episode_numbers(self, tgl_episodes: list) -> dict:
        """Infer episode numbers for TGL episodes without explicit numbers

        Args:
            tgl_episodes: List of TGL episode data dicts sorted by published date

        Returns:
            Dict mapping episode link to inferred episode number
        """
        inferred = {}

        # First, extract all known episode numbers with their positions
        known_episodes = []
        for idx, ep in enumerate(tgl_episodes):
            ep_num = self.parse_episode_id(ep['title'])
            if ep_num is not None:
                known_episodes.append((idx, ep_num, ep['link']))

        # If no known episodes, can't infer
        if not known_episodes:
            return inferred

        # For each episode without a number, infer based on position
        for idx, ep in enumerate(tgl_episodes):
            ep_num = self.parse_episode_id(ep['title'])
            if ep_num is None:
                # Find surrounding known episodes
                prev_known = None
                next_known = None

                for known_idx, known_num, known_link in known_episodes:
                    if known_idx < idx:
                        prev_known = (known_idx, known_num)
                    elif known_idx > idx and next_known is None:
                        next_known = (known_idx, known_num)
                        break

                # Infer based on position
                if prev_known and next_known:
                    # Between two known episodes - interpolate
                    prev_idx, prev_num = prev_known
                    next_idx, next_num = next_known

                    # Calculate position ratio
                    total_gap = next_idx - prev_idx
                    position_in_gap = idx - prev_idx

                    # Interpolate episode number
                    num_gap = next_num - prev_num
                    inferred_num = prev_num + round((num_gap * position_in_gap) / total_gap)
                    inferred[ep['link']] = inferred_num

                elif prev_known:
                    # After last known episode - increment
                    prev_idx, prev_num = prev_known
                    gap = idx - prev_idx
                    inferred[ep['link']] = prev_num + gap

                elif next_known:
                    # Before first known episode - decrement
                    next_idx, next_num = next_known
                    gap = next_idx - idx
                    inferred[ep['link']] = max(1, next_num - gap)

        return inferred

    def classify_episode_type(self, title: str) -> str:
        """Classify episode as TGL or BONUS based on title

        Priority order:
        1. Check for clear TGL indicators with episode numbers (HIGHEST PRIORITY)
        2. Check for BONUS-only patterns (never TGL)
        3. Check for broader TGL patterns (without requiring numbers)
        4. Default to BONUS
        """
        title_lower = title.lower()

        # First priority: Clear TGL episode patterns with numbers
        # These take precedence over ANY other content patterns
        clear_tgl_patterns = [
            r'\btgl\s+\d+',        # "TGL 227", "TGL 126"
            r'\btgl\s*-\s*\d+',    # "TGL - 169", "TGL-169" (dash-separated format)
            r'\btgl\d+:',          # "TGL221:" (no space before colon)
            r'\btgl:\s*e\s*\d+',   # "TGL: E242" (colon before E number)
            r'\btgl\s+e\d+',       # "TGL E390" etc.
            r'\btgl\s+episode\s+\d+',  # "TGL Episode 208"
            r'\bgue[^-\s]*list\s*[:-]?\s*episode\s+\d+',  # "Guestlist: Episode 140", "Gueslist - Episode 100" (typo-tolerant)
            r'\bg-?list\s*[:-]?\s*episode\s+\d+',    # "G-list - Episode 95"
            r'\bguestlist\s+\d+',  # "The Guestlist 47"
            r'\bg-?list\s+\d+',    # "G-list 95"
            r'\(e\d+\)',           # "(e117)" - episode number in parentheses
            r'\be\d{3,}\b',        # "E187", "E117" - three or more digits (likely TGL, not random E prefix)
        ]

        for pattern in clear_tgl_patterns:
            if re.search(pattern, title_lower):
                return 'TGL'

        # Second priority: BONUS-only patterns (not TGL episodes)
        # These are content types that are never TGL episodes
        bonus_only_patterns = [
            r'\bfear of tigers\b',
            r'\bfot\b',  # Fear of Tigers abbreviation
            r'\btrailer\b',
            r'\bextra\b',  # "The Guestlist Extra" etc.
            r'\blistening guide\b',  # "Echo Drop EP and listening guide" etc.
            r'\brunners?\s+club\b',  # "Runners Club" or "Runner Club"
            r'\bre-?edit\b',  # Re-edits
            r'\bguestmix\b',  # Guest mixes
            r'\boriginal music\b',  # Original music releases
            r'\bnew song\b',  # New song announcements
            r'\balbum\b',  # Album announcements/releases
            r'\bcossus\b',  # "Cossus" series (production music)
            r'\bmaking of\b',  # "Making of" episodes
            r'\bre-?up\b',
            r'\binterview\b',
        ]

        for pattern in bonus_only_patterns:
            if re.search(pattern, title_lower):
                return 'BONUS'

        # Third priority: Broader TGL patterns without requiring numbers
        # (for episodes that might not have clear numbering)
        broad_tgl_patterns = [
            r'\btgl\b',
            r'\bguestlist\b',
            r'\bg-?list\b',
        ]

        for pattern in broad_tgl_patterns:
            if re.search(pattern, title_lower):
                return 'TGL'

        # If title has E## (2 digits or less) but no TGL keywords, it's likely BONUS
        # (E### with 3+ digits is already handled above as TGL)
        if re.search(r'\be[\s:]*\d{1,2}\b', title_lower):
            return 'BONUS'

        # Default to BONUS for ambiguous cases (safer to not overwrite TGL episodes)
        return 'BONUS'

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

    def _format_duration(self, seconds: int) -> str:
        """Format duration in seconds to HH:MM:SS or MM:SS format"""
        if not seconds or seconds <= 0:
            return None

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes}:{secs:02d}"

    def _get_audio_duration(self, audio_url: str) -> Optional[str]:
        """Get duration from audio file by downloading and parsing it

        Downloads the full audio file to accurately determine duration.
        This is cached so it only happens once per episode.
        """
        if not MUTAGEN_AVAILABLE or not audio_url:
            return None

        try:
            # Download the file
            headers = {
                'User-Agent': 'Mozilla/5.0 (compatible; TGL-CLI/1.0)',
            }

            response = requests.get(audio_url, headers=headers, timeout=30, stream=True)

            if response.status_code == 200:
                # Read the content into memory
                audio_data = BytesIO()
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        audio_data.write(chunk)

                # Reset to beginning
                audio_data.seek(0)

                try:
                    # Parse MP3 metadata
                    audio = MP3(audio_data)
                    if audio.info and audio.info.length:
                        return self._format_duration(int(audio.info.length))
                except (ID3NoHeaderError, Exception) as e:
                    # Failed to parse
                    pass

            return None

        except Exception as e:
            # Silently fail - duration is optional
            return None

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

                # Store original title from RSS
                original_title = title

                # Classify episode type
                episode_type = self.classify_episode_type(title)

                # Extract clean title (after colon)
                clean_title = title
                if ':' in title:
                    clean_title = title.split(':', 1)[1].strip()

                # Further clean the title to remove episode numbers and podcast name
                clean_title = self._clean_episode_title(clean_title)

                # Parse published date
                published_parsed = entry.get('published_parsed')
                year = None
                published = entry.get('published', '')
                if published_parsed:
                    year = published_parsed.tm_year
                    published = time.strftime('%Y-%m-%d', published_parsed)

                # Get audio URL, file size, and duration
                audio_url = None
                audio_size = None
                duration = None
                if hasattr(entry, 'enclosures') and entry.enclosures:
                    audio_url = entry.enclosures[0].get('href')
                    # Get file size if available (in bytes)
                    if 'length' in entry.enclosures[0]:
                        try:
                            audio_size = int(entry.enclosures[0]['length'])
                        except (ValueError, TypeError):
                            pass

                # Try to get duration from itunes:duration first
                if hasattr(entry, 'itunes_duration'):
                    # itunes_duration can be in HH:MM:SS format or seconds
                    duration_value = entry.itunes_duration
                    if ':' in str(duration_value):
                        # Already formatted
                        duration = duration_value
                    else:
                        # Convert seconds to formatted string
                        try:
                            duration = self._format_duration(int(duration_value))
                        except (ValueError, TypeError):
                            pass

                # Duration extraction from audio files is disabled by default
                # as it requires downloading ~60MB per episode (slow and bandwidth-intensive)
                # Duration will remain None unless RSS feed includes it

                # Get full description
                raw_description = entry.get('description', '') or entry.get('summary', '')

                # Parse description text and tracklist
                description_text = self._extract_description_text(raw_description)
                tracklist = self._parse_structured_tracklist(raw_description)

                # Get guid (or id) for episode identity tracking
                # RSS feeds use guid or id for unique identity
                guid = entry.get('guid') or entry.get('id') or entry.get('link', '')

                # Store episode data temporarily
                temp_episodes.append({
                    'title': title,
                    'original_title': original_title,
                    'clean_title': clean_title,
                    'episode_type': episode_type,
                    'published': published,
                    'published_parsed': published_parsed,
                    'year': year,
                    'link': entry.get('link', ''),
                    'guid': guid,
                    'audio_url': audio_url,
                    'audio_size': audio_size,
                    'duration': duration,
                    'description': raw_description,
                    'description_text': description_text,
                    'tracklist': tracklist
                })

            # Second pass: Track which TGL numbers are used as we assign them
            # This prevents duplicate episode IDs
            used_tgl_numbers = set()

            # Third pass: Run inference to fill gaps in TGL sequence
            # Sort TGL episodes by published date for inference
            tgl_episodes = [ep for ep in temp_episodes if ep['episode_type'] == 'TGL']
            tgl_episodes.sort(key=lambda ep: ep['published_parsed'] if ep['published_parsed'] else time.struct_time((1970, 1, 1, 0, 0, 0, 0, 1, 0)))
            inferred_numbers = self._infer_episode_numbers(tgl_episodes)

            # Fourth pass: Try to fill gaps with BONUS episodes
            # Get all explicit TGL numbers
            explicit_tgl_numbers = set()
            for ep in tgl_episodes:
                ep_num = self.parse_episode_id(ep['title'])
                if ep_num:
                    explicit_tgl_numbers.add(ep_num)

            # Run inference on ALL episodes to find chronological positions
            all_sorted = sorted(temp_episodes, key=lambda ep: ep['published_parsed'] if ep['published_parsed'] else time.struct_time((1970, 1, 1, 0, 0, 0, 0, 1, 0)))
            all_inferred = self._infer_episode_numbers(all_sorted)

            # Reclassify BONUS episodes that fill TGL gaps
            for ep_data in temp_episodes:
                if ep_data['episode_type'] == 'BONUS':
                    inferred_num = all_inferred.get(ep_data['link'])
                    # Only reclassify if:
                    # 1. Has an inferred number
                    # 2. That number is not explicitly used by a TGL episode
                    if inferred_num and inferred_num not in explicit_tgl_numbers:
                        ep_data['episode_type'] = 'TGL'
                        inferred_numbers[ep_data['link']] = inferred_num

            # Fifth pass: Assign B numbers to BONUS episodes
            bonus_episodes = [ep for ep in temp_episodes if ep['episode_type'] == 'BONUS']
            bonus_episodes.sort(key=lambda ep: ep['published_parsed'] if ep['published_parsed'] else time.struct_time((1970, 1, 1, 0, 0, 0, 0, 1, 0)))

            # Create mapping from link to B number
            bonus_id_map = {}
            for idx, ep in enumerate(bonus_episodes, start=1):
                bonus_id_map[ep['link']] = idx

            # Sixth pass: create Episode objects with proper IDs and prevent duplicates
            episodes = []
            for ep_data in temp_episodes:
                episode_type = ep_data['episode_type']

                # Hardcoded reclassifications (special cases)
                if 'From The Crates - Euphoric Piano House 1994-1995' in ep_data['title']:
                    # Force this episode to be E50 (TGL)
                    episode_type = 'TGL'
                elif 'FOT Cast - What Should Fear of Tigers remix next?' in ep_data['title']:
                    # Current E50 should be demoted to BONUS
                    episode_type = 'BONUS'

                if episode_type == 'TGL':
                    # Get episode number from title
                    numeric_id = self.parse_episode_id(ep_data['title'])

                    # Special handling for hardcoded episode numbers
                    if 'From The Crates - Euphoric Piano House 1994-1995' in ep_data['title']:
                        # Force this to be E50
                        numeric_id = 50
                    elif 'The Best of 2019 - Listeners Choice' in ep_data['title'] and numeric_id == 119:
                        # E119 "The Best of 2019 - Listeners Choice" -> E118
                        numeric_id = 118
                    elif 'Old School Delight' in ep_data['title'] and numeric_id == 126:
                        # E126 "Old School Delight" -> E226 (correct episode number)
                        numeric_id = 226

                    # If no explicit number, try inference
                    if numeric_id is None:
                        numeric_id = inferred_numbers.get(ep_data['link'], 0)

                    # Check if this is a hardcoded episode (allowed to claim its number)
                    is_hardcoded = (
                        'From The Crates - Euphoric Piano House 1994-1995' in ep_data['title'] or
                        'The Best of 2019 - Listeners Choice' in ep_data['title'] or
                        'Old School Delight' in ep_data['title']
                    )

                    # Check if this number is already used (unless this is a hardcoded episode)
                    if numeric_id > 0 and numeric_id in used_tgl_numbers and not is_hardcoded:
                        # Duplicate! Fall back to BONUS
                        episode_type = 'BONUS'
                        b_number = len(bonus_id_map) + 1
                        bonus_id_map[ep_data['link']] = b_number
                        numeric_id = 10000 + b_number
                        episode_id_str = f"B{b_number:02d}"
                    elif numeric_id > 0:
                        # Valid unique TGL number
                        used_tgl_numbers.add(numeric_id)
                        episode_id_str = f"E{numeric_id}"
                    else:
                        # No number at all, fall back to BONUS
                        episode_type = 'BONUS'
                        b_number = len(bonus_id_map) + 1
                        bonus_id_map[ep_data['link']] = b_number
                        numeric_id = 10000 + b_number
                        episode_id_str = f"B{b_number:02d}"
                else:
                    # BONUS episodes use sequential numbering with offset to avoid conflicts
                    # Use 10000 + sequence number as the internal numeric ID
                    b_number = bonus_id_map.get(ep_data['link'])
                    if b_number is None:
                        # This shouldn't happen, but fallback just in case
                        b_number = len(bonus_id_map) + 1
                        bonus_id_map[ep_data['link']] = b_number
                    numeric_id = 10000 + b_number
                    episode_id_str = f"B{b_number:02d}"

                # Use original RSS title as full_title
                full_title = ep_data['original_title']

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
                    guid=ep_data['guid'],
                    audio_url=ep_data['audio_url'],
                    audio_size=ep_data['audio_size'],
                    episode_type=episode_type,
                    duration=ep_data['duration']
                )
                episodes.append(episode)

            return episodes

        except requests.exceptions.RequestException as e:
            console.print(f"[red]Error fetching RSS feed: {e}[/red]")
            return []
