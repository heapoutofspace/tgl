"""Unit tests for episode classification logic

Tests the classify_episode_type function to prevent regressions in episode classification.
"""

import pytest
from tgl.fetcher import PatreonPodcastFetcher


class TestEpisodeClassification:
    """Test episode type classification"""

    @pytest.fixture
    def fetcher(self):
        """Create a fetcher instance for testing"""
        return PatreonPodcastFetcher(rss_url="dummy")

    # =========================================================================
    # TGL Episodes - "Best Of" compilations with episode numbers
    # =========================================================================

    def test_best_of_2018_episode_48(self, fetcher):
        """Best of 2018 part 1 should be TGL (has explicit episode number)"""
        title = "The Guestlist - Episode 48 - Best of 2018 so far"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"Episode 48 'Best of 2018' should be TGL, got {result}"

    def test_best_of_2018_episode_49(self, fetcher):
        """Best of 2018 part 2 should be TGL (has explicit episode number)"""
        title = "The Guestlist - Episode 49 - Best of 2018 so far part 2"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"Episode 49 'Best of 2018' should be TGL, got {result}"

    def test_best_of_2018_episode_70(self, fetcher):
        """Best of 2018 episode 70 should be TGL (has explicit episode number)"""
        title = "The Guestlist Episode 70 - The Best of 2018!"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"Episode 70 'Best of 2018' should be TGL, got {result}"

    def test_gueslist_episode_100(self, fetcher):
        """Episode 100 with typo should be TGL (has explicit episode number)"""
        title = "The Gueslist - Episode 100"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"Episode 100 should be TGL, got {result}"

    def test_best_of_2019_episode_119(self, fetcher):
        """Best of 2019 episode 119 should be TGL (has episode number in parentheses)"""
        title = "The Best of 2019 - Listeners Choice (the guestlist e119)"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"Episode 119 'Best of 2019' should be TGL, got {result}"

    def test_best_dancefloor_2019_episode_117(self, fetcher):
        """Best Dancefloor Tracks 2019 should be TGL (has episode number in parentheses)"""
        title = "The Best Dancefloor Tracks of 2019 (e117)"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"Episode 117 'Best Dancefloor Tracks' should be TGL, got {result}"

    def test_best_of_winter_2020_episode_132(self, fetcher):
        """Best of Winter 2020 episode 132 should be TGL (has explicit episode number)"""
        title = "TGL: E132 - The Best of Winter 2020 (the everything burrito edition)"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"Episode 132 'Best of Winter 2020' should be TGL, got {result}"

    # =========================================================================
    # TGL Episodes - Standard formats
    # =========================================================================

    def test_standard_tgl_e390(self, fetcher):
        """Standard TGL E390 format"""
        title = "TGL E390: Love Songs and Haunted Nights"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"TGL E390 should be TGL, got {result}"

    def test_standard_guestlist_227(self, fetcher):
        """TGL 227 with content keyword"""
        title = "TGL 227: From the Crates - Speed Garage Special"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"TGL 227 should be TGL, got {result}"

    def test_standard_guestlist_episode_208(self, fetcher):
        """TGL Episode 208 with colon"""
        title = "TGL Episode 208:"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"TGL Episode 208 should be TGL, got {result}"

    def test_standard_guestlist_episode_140(self, fetcher):
        """The Guestlist: Episode 140"""
        title = "The Guestlist: Episode 140"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"The Guestlist: Episode 140 should be TGL, got {result}"

    def test_standard_guestlist_47(self, fetcher):
        """The Guestlist 47"""
        title = "The Guestlist 47"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"The Guestlist 47 should be TGL, got {result}"

    def test_glist_95(self, fetcher):
        """G-list 95"""
        title = "G-list 95"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"G-list 95 should be TGL, got {result}"

    def test_tgl_126_old_school(self, fetcher):
        """TGL 126 with Old School in title"""
        title = "TGL 126: Old School Delight"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"TGL 126 should be TGL, got {result}"

    def test_tgl_187_from_the_crates(self, fetcher):
        """E187 From The Crates"""
        title = "E187 - From The Crates, Old School Madness"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"E187 should be TGL (has E prefix in context), got {result}"

    # =========================================================================
    # BONUS Episodes - Re-uploads with old episode numbers
    # =========================================================================

    def test_back_to_school_reupload(self, fetcher):
        """Back to School Classics is a re-upload, should be BONUS"""
        title = "TGL E152: Back to School Classics"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Back to School re-upload should be BONUS, got {result}"

    def test_stormaggedeon_reupload(self, fetcher):
        """STORMAGGEDEON is a re-upload, should be BONUS"""
        title = "TGL E122: STORMAGGEDEON!"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"STORMAGGEDEON re-upload should be BONUS, got {result}"

    def test_rewind_episode(self, fetcher):
        """Rewind episodes are re-uploads, should be BONUS"""
        title = "TGL Rewind - Day Trip To Berlin"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Rewind episode should be BONUS, got {result}"

    def test_pure_fire_edition(self, fetcher):
        """Pure Fire Edition is a re-upload, should be BONUS"""
        title = "TGL E212 - PURE FIRE EDITION!"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Pure Fire Edition should be BONUS, got {result}"

    def test_mark_runs_marathon(self, fetcher):
        """Mark Runs the Marathon is personal content, should be BONUS"""
        title = "TGL E367 - Mark Runs the Marathon"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Mark Runs episode should be BONUS, got {result}"

    # =========================================================================
    # BONUS Episodes - Special content
    # =========================================================================

    def test_fear_of_tigers(self, fetcher):
        """Fear of Tigers releases should be BONUS"""
        title = "Fear of Tigers - Echo Drop"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Fear of Tigers should be BONUS, got {result}"

    def test_fot_abbreviation(self, fetcher):
        """FOT abbreviation should be BONUS"""
        title = "FOT Cast - What Should Fear of Tigers remix next?"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"FOT Cast should be BONUS, got {result}"

    def test_guestlist_extra(self, fetcher):
        """The Guestlist Extra should be BONUS"""
        title = "The Guestlist Extra"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Guestlist Extra should be BONUS, got {result}"

    def test_trailer(self, fetcher):
        """Trailer should be BONUS"""
        title = "Lo-Fi Belgrade Trailer"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Trailer should be BONUS, got {result}"

    def test_listening_guide(self, fetcher):
        """Listening guide should be BONUS"""
        title = "Echo Drop EP and listening guide"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Listening guide should be BONUS, got {result}"

    def test_original_music(self, fetcher):
        """Original music releases should be BONUS"""
        title = "Original music - Down To The Sea.. And Back (Ben's re-edit)"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Original music should be BONUS, got {result}"

    def test_cossus_series(self, fetcher):
        """Cossus series should be BONUS"""
        title = "Cossus Part II - The Making of Episode 1"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Cossus should be BONUS, got {result}"

    def test_album_announcement(self, fetcher):
        """Album announcements should be BONUS"""
        title = "New FOT album update and preview"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Album announcement should be BONUS, got {result}"

    def test_interview_content(self, fetcher):
        """Interview content should be BONUS"""
        title = "Interview with Mood II Swing"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Interview should be BONUS, got {result}"

    # =========================================================================
    # Edge Cases
    # =========================================================================

    def test_e_prefix_without_guestlist_keywords(self, fetcher):
        """E### prefix without guestlist keywords should be BONUS"""
        title = "E25 - What goes on in Buda stays in Buda..."
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"E### without guestlist keywords should be BONUS, got {result}"

    def test_ambiguous_title(self, fetcher):
        """Ambiguous title defaults to BONUS"""
        title = "Random Music Mix"
        result = fetcher.classify_episode_type(title)
        assert result == 'BONUS', f"Ambiguous title should default to BONUS, got {result}"

    def test_case_insensitive_matching(self, fetcher):
        """Classification should be case-insensitive"""
        title = "tgl e390: LOVE SONGS"
        result = fetcher.classify_episode_type(title)
        assert result == 'TGL', f"Lowercase TGL should be TGL, got {result}"


class TestEpisodeIDParsing:
    """Test episode ID parsing from titles"""

    @pytest.fixture
    def fetcher(self):
        """Create a fetcher instance for testing"""
        return PatreonPodcastFetcher(rss_url="dummy")

    def test_parse_episode_48(self, fetcher):
        """Parse episode number from 'Episode 48' format"""
        title = "The Guestlist - Episode 48 - Best of 2018 so far"
        result = fetcher.parse_episode_id(title)
        assert result == 48, f"Should parse 48, got {result}"

    def test_parse_episode_70_with_typo(self, fetcher):
        """Parse episode number from 'Episode 70' with 'Guestlist' typo"""
        title = "The Guestlist Episode 70 - The Best of 2018!"
        result = fetcher.parse_episode_id(title)
        assert result == 70, f"Should parse 70, got {result}"

    def test_parse_episode_100_with_typo(self, fetcher):
        """Parse episode number with 'Gueslist' typo"""
        title = "The Gueslist - Episode 100"
        result = fetcher.parse_episode_id(title)
        assert result == 100, f"Should parse 100, got {result}"

    def test_parse_e119_parentheses(self, fetcher):
        """Parse episode number from parenthetical (e119)"""
        title = "The Best of 2019 - Listeners Choice (the guestlist e119)"
        result = fetcher.parse_episode_id(title)
        assert result == 119, f"Should parse 119, got {result}"

    def test_parse_e117_parentheses(self, fetcher):
        """Parse episode number from (e117)"""
        title = "The Best Dancefloor Tracks of 2019 (e117)"
        result = fetcher.parse_episode_id(title)
        assert result == 117, f"Should parse 117, got {result}"

    def test_parse_tgl_e132(self, fetcher):
        """Parse episode number from TGL: E132 format"""
        title = "TGL: E132 - The Best of Winter 2020 (the everything burrito edition)"
        result = fetcher.parse_episode_id(title)
        assert result == 132, f"Should parse 132, got {result}"

    def test_parse_tgl_episode_208(self, fetcher):
        """Parse TGL Episode 208"""
        title = "TGL Episode 208:"
        result = fetcher.parse_episode_id(title)
        assert result == 208, f"Should parse 208, got {result}"

    def test_parse_guestlist_colon_140(self, fetcher):
        """Parse The Guestlist: Episode 140"""
        title = "The Guestlist: Episode 140"
        result = fetcher.parse_episode_id(title)
        assert result == 140, f"Should parse 140, got {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
