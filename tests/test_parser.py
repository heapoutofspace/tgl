"""
Unit tests for TGL track and ID parsing logic

Run with: pytest tests/
"""

import pytest
from tgl import TrackInfo, parse_episode_id
from tgl.fetcher import PatreonPodcastFetcher


class TestEpisodeIDParsing:
    """Tests for parse_episode_id function"""

    def test_parse_plain_number(self):
        """Plain numbers should default to TGL episodes"""
        assert parse_episode_id("390") == 390
        assert parse_episode_id("1") == 1
        assert parse_episode_id("999") == 999

    def test_parse_e_prefix(self):
        """E-prefixed IDs should parse as TGL episodes"""
        assert parse_episode_id("E390") == 390
        assert parse_episode_id("E1") == 1
        assert parse_episode_id("e150") == 150  # lowercase should work

    def test_parse_b_prefix(self):
        """B-prefixed IDs should parse as BONUS episodes (10000 + number)"""
        assert parse_episode_id("B01") == 10001
        assert parse_episode_id("B05") == 10005
        assert parse_episode_id("B99") == 10099
        assert parse_episode_id("b10") == 10010  # lowercase should work

    def test_parse_with_whitespace(self):
        """Should handle whitespace gracefully"""
        assert parse_episode_id("  E390  ") == 390
        assert parse_episode_id(" B05 ") == 10005

    def test_invalid_format(self):
        """Invalid formats should raise ValueError"""
        with pytest.raises(ValueError):
            parse_episode_id("ABC")
        with pytest.raises(ValueError):
            parse_episode_id("E")
        with pytest.raises(ValueError):
            parse_episode_id("")


class TestTracklistParsing:
    """Tests for tracklist parsing logic"""

    @pytest.fixture
    def fetcher(self):
        """Create a PatreonPodcastFetcher instance for testing"""
        # We don't need a real RSS URL for parsing tests
        return PatreonPodcastFetcher("https://example.com/rss")

    def test_basic_track_parsing(self, fetcher):
        """Should parse basic Artist - Track format"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Prospa - Love Songs</p>
        <p># Notre Dame - Haunted Nights</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 2
        assert tracks[0].artist == "Prospa"
        assert tracks[0].title == "Love Songs"
        assert tracks[0].variant is None
        assert tracks[1].artist == "Notre Dame"
        assert tracks[1].title == "Haunted Nights"

    def test_tracks_with_variants(self, fetcher):
        """Should extract remix/feat info into variant field"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Tuba Rex - The Magnetic Empire (Pianopoli Remix)</p>
        <p># Prospa - Love Songs (feat. Kosmo Kint)</p>
        <p># Lord Leopard & Little Boots - Spin Back (Extended Mix)</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 3
        assert tracks[0].title == "The Magnetic Empire"
        assert tracks[0].variant == "Pianopoli Remix"
        assert tracks[1].title == "Love Songs"
        assert tracks[1].variant == "feat. Kosmo Kint"
        assert tracks[2].title == "Spin Back"
        assert tracks[2].variant == "Extended Mix"

    def test_prose_filtering_e340_issue(self, fetcher):
        """Should filter out prose that looks like tracks (E340 issue)"""
        html = """
        <p>On this week's episode of The Guestlist we're channeling the hot summer heat - all new music this week. Expect Nu-Italo, deep and dirty house and even a little bit of Donk.</p>
        <p><strong>Tracklist</strong></p>
        <p># Kiimi, Taite Impgen - History (feat. Taite Imogen)</p>
        <p># Azzecca - IDK</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        # Should only get the 2 tracks, not the prose
        assert len(tracks) == 2
        assert tracks[0].artist == "Kiimi, Taite Impgen"
        assert tracks[1].artist == "Azzecca"

        # Verify the prose isn't in there
        for track in tracks:
            assert "channeling" not in track.artist.lower()
            assert "expect" not in track.artist.lower()

    def test_explicit_tracklist_marker_required(self, fetcher):
        """After explicit 'Tracklist' marker, should prefer explicit marker"""
        html = """
        <p>Some prose text that shouldn't be parsed</p>
        <p><strong>Tracklist</strong></p>
        <p># Real Artist - Real Track</p>
        <p># Another Artist - Another Track</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        # Should get the real tracks, not the prose
        assert len(tracks) >= 2
        assert any(track.artist == "Real Artist" for track in tracks)
        assert any(track.artist == "Another Artist" for track in tracks)

    def test_numbered_tracks(self, fetcher):
        """Should handle various numbering formats"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p>1. Artist One - Track One</p>
        <p>2) Artist Two - Track Two</p>
        <p>3 Artist Three - Track Three</p>
        <p># Artist Four - Track Four</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 4
        assert tracks[0].artist == "Artist One"
        assert tracks[1].artist == "Artist Two"
        assert tracks[2].artist == "Artist Three"
        assert tracks[3].artist == "Artist Four"

    def test_html_entities(self, fetcher):
        """Should properly handle HTML entities"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Hanna Laing &amp; Muki - Ibizacore</p>
        <p># Rex The Dog, Airwolf Paradise - Son of a Gun (feat JX)</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 2
        assert "&" in tracks[0].artist  # Should be decoded from &amp;
        assert tracks[0].artist == "Hanna Laing & Muki"

    def test_original_mix_removal(self, fetcher):
        """Should not store 'Original Mix' as variant (it's the default)"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Artist - Track (Original Mix)</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 1
        assert tracks[0].variant is None  # Original Mix should be None

    def test_special_prefixes(self, fetcher):
        """Should handle special prefixes like 'Record of the Week'"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p>RECORD OF THE WEEK: Artist - Amazing Track</p>
        <p>FROM THE CRATES: Classic Artist - Old Track</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 2
        assert tracks[0].artist == "Artist"
        assert tracks[0].title == "Amazing Track"
        assert tracks[1].artist == "Classic Artist"

    def test_url_filtering(self, fetcher):
        """Should skip lines containing URLs"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Artist - Track</p>
        <p># Listen at http://example.com - Not A Track</p>
        <p># Another Artist - Another Track</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 2
        assert tracks[0].artist == "Artist"
        assert tracks[1].artist == "Another Artist"

    def test_duplicate_tracks_filtered(self, fetcher):
        """Should not include duplicate tracks"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Artist - Track</p>
        <p># Artist - Track</p>
        <p># Different Artist - Different Track</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 2
        assert tracks[0].artist == "Artist"
        assert tracks[1].artist == "Different Artist"

    def test_section_boundaries(self, fetcher):
        """Should stop parsing at section boundaries"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Artist - Track One</p>
        <p># Artist - Track Two</p>
        <p>----</p>
        <p>Best of the Year:</p>
        <p># Not A Track - Should Not Parse</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 2
        assert all(track.title in ["Track One", "Track Two"] for track in tracks)

    def test_complex_artist_names(self, fetcher):
        """Should handle complex artist names with multiple collaborators"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Louis de Tomaso - Jo Kazan &amp; Fabrizio Mammarella - Gj</p>
        <p># Dina Summer, Kalipo, Local Suicide - Halkidiki</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 2
        # These complex formats should be parsed
        assert "Louis de Tomaso" in tracks[0].artist
        assert "Dina Summer" in tracks[1].artist

    def test_no_tracklist_marker(self, fetcher):
        """Should detect implicit tracklist (3+ consecutive tracks)"""
        html = """
        <p>Some description text</p>
        <p># Artist One - Track One</p>
        <p># Artist Two - Track Two</p>
        <p># Artist Three - Track Three</p>
        <p># Artist Four - Track Four</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        # Should detect implicit tracklist and parse all tracks
        assert len(tracks) >= 3

    def test_edge_case_single_letter_artist(self, fetcher):
        """Should skip tracks with too-short artist or title"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># A - Track</p>
        <p># Artist - B</p>
        <p># Real Artist - Real Track</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        # Should only get the valid track
        assert len(tracks) == 1
        assert tracks[0].artist == "Real Artist"

    def test_various_dash_types(self, fetcher):
        """Should handle different dash characters (hyphen, en dash, em dash)"""
        html = """
        <p><strong>Tracklist</strong></p>
        <p># Artist One - Track One</p>
        <p># Artist Two – Track Two</p>
        <p># Artist Three — Track Three</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        assert len(tracks) == 3
        # All three dash types should be recognized
        assert tracks[0].artist == "Artist One"
        assert tracks[1].artist == "Artist Two"
        assert tracks[2].artist == "Artist Three"

    def test_no_prefix_implicit_tracklist_e64_issue(self, fetcher):
        """Should detect implicit tracklist without # or numbers (E64 issue)"""
        html = """
        <p>Sammy Bananas &amp; Kaleena Zanders - Cherry Soda</p>
        <p>Duke Dumont - Runway</p>
        <p>Gabe Gurnsey - You Can (The Hacker remix)</p>
        <p>Gabe Gurnsey - You Can (Extended Dub)</p>
        <p>These Machines - Sometimes</p>
        <p>RECORD OF THE WEEK: Piem, Alaia &amp; Gallo - All The Things</p>
        <p>Utah Saints - Something Good (VanShe Tech Mix)</p>
        <p>FROM THE CRATES: ITALO / NORTHERN PIANO SPECIAL</p>
        <p>Brothers In Rhythm - Peace And Harmony (Italo's Grand Finale)</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        # Should detect multiple consecutive "Artist - Track" lines as implicit tracklist
        assert len(tracks) >= 5
        assert tracks[0].artist == "Sammy Bananas & Kaleena Zanders"
        assert tracks[0].title == "Cherry Soda"
        assert tracks[1].artist == "Duke Dumont"
        assert tracks[1].title == "Runway"

        # Should handle RECORD OF THE WEEK prefix
        rotw_track = next((t for t in tracks if "Piem" in t.artist), None)
        assert rotw_track is not None
        assert rotw_track.artist == "Piem, Alaia & Gallo"

        # Should handle variants
        hacker_remix = next((t for t in tracks if t.variant and "Hacker" in t.variant), None)
        assert hacker_remix is not None

    def test_artist_names_with_common_words_e75_issue(self, fetcher):
        """Should not filter out artist names containing common words (E75 issue)"""
        html = """
        <p>Tensnake and Jacques Lu Cont - Feel of Love</p>
        <p>Tenven - Just About (Prospa Remix)</p>
        <p>The Presets - Yippiyo-Ay</p>
        <p>Tchami - Aurra</p>
        <p>Shakedown - At Night (Purple Disco Machine remix)</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        # Should parse all tracks including those with "and" and "The"
        assert len(tracks) >= 5

        # First track has "and" in artist name - should NOT be filtered as prose
        first_track = next((t for t in tracks if "Tensnake" in t.artist), None)
        assert first_track is not None
        assert first_track.artist == "Tensnake and Jacques Lu Cont"
        assert first_track.title == "Feel of Love"

        # Artist starting with "The" should NOT be filtered
        presets_track = next((t for t in tracks if "Presets" in t.artist), None)
        assert presets_track is not None
        assert presets_track.artist == "The Presets"

    def test_from_the_blogs_prefix_e61_issue(self, fetcher):
        """Should handle FROM THE BLOGS and TRACK OF THE WEEK prefixes (E61 issue)"""
        html = """
        <p>FROM THE BLOGS: Melinda Jackson - Magic (Moustache Remix)</p>
        <p>Skibblez - Need Someone</p>
        <p>Matt Zo - Fall Into Dreams (2012 Club mix)</p>
        <p>FROM THE BLOGS: Amtrac - All Night</p>
        <p>TRACK OF THE WEEK: The Phantom's Revenge - Sunset 3 Hundred</p>
        <p>FROM THE BLOGS: Belle - What The Hell (Lifelike and Kris Menace remix)</p>
        <p>Christine and The Queens - Le marcheuse</p>
        """
        tracks = fetcher._parse_structured_tracklist(html)

        # Should parse all tracks
        assert len(tracks) >= 5

        # Should handle FROM THE BLOGS prefix
        melinda = next((t for t in tracks if "Melinda" in t.artist), None)
        assert melinda is not None
        assert melinda.artist == "Melinda Jackson"
        assert melinda.variant == "Moustache Remix"

        # Should handle TRACK OF THE WEEK prefix
        phantom = next((t for t in tracks if "Phantom" in t.artist), None)
        assert phantom is not None
        assert phantom.artist == "The Phantom's Revenge"

        # Artist with "and The" should work
        christine = next((t for t in tracks if "Christine" in t.artist), None)
        assert christine is not None
        assert christine.artist == "Christine and The Queens"


class TestTrackInfoModel:
    """Tests for the TrackInfo pydantic model"""

    def test_basic_track_creation(self):
        """Should create track with required fields"""
        track = TrackInfo(artist="Prospa", title="Love Songs")
        assert track.artist == "Prospa"
        assert track.title == "Love Songs"
        assert track.variant is None

    def test_track_with_variant(self):
        """Should create track with variant"""
        track = TrackInfo(
            artist="Tuba Rex",
            title="The Magnetic Empire",
            variant="Pianopoli Remix"
        )
        assert track.variant == "Pianopoli Remix"

    def test_track_serialization(self):
        """Should serialize to dict correctly"""
        track = TrackInfo(
            artist="Artist",
            title="Title",
            variant="Remix"
        )
        data = track.model_dump()
        assert data["artist"] == "Artist"
        assert data["title"] == "Title"
        assert data["variant"] == "Remix"

    def test_track_without_variant_serialization(self):
        """Should serialize None variant correctly"""
        track = TrackInfo(artist="Artist", title="Title")
        data = track.model_dump()
        assert data["variant"] is None


if __name__ == "__main__":
    # Allow running directly with: python test_tgl.py
    pytest.main([__file__, "-v"])
