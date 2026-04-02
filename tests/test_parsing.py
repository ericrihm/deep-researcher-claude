from deep_researcher.parsing import (
    build_tiered_corpus,
    parse_categories,
    parse_merged_categories,
    titles_match,
)
from deep_researcher.models import Paper


class TestParseCategories:
    def test_basic_parsing(self):
        text = "CATEGORY: Vision Methods\nPAPERS: 1, 2, 3\n\nCATEGORY: NLP Methods\nPAPERS: 4, 5"
        result = parse_categories(text, 5)
        assert "Vision Methods" in result
        assert "NLP Methods" in result
        assert result["Vision Methods"] == [0, 1, 2]

    def test_strips_markdown_bold(self):
        text = "**CATEGORY:** Vision Methods\n**PAPERS:** 1, 2, 3"
        result = parse_categories(text, 3)
        assert "Vision Methods" in result

    def test_strips_list_markers(self):
        text = "- CATEGORY: Vision Methods\n- PAPERS: 1, 2"
        result = parse_categories(text, 2)
        assert "Vision Methods" in result

    def test_case_insensitive(self):
        text = "category: Test\npapers: 1, 2"
        result = parse_categories(text, 2)
        assert "Test" in result

    def test_ignores_out_of_range(self):
        text = "CATEGORY: Test\nPAPERS: 1, 2, 99"
        result = parse_categories(text, 5)
        assert 98 not in result["Test"]

    def test_empty_input(self):
        assert parse_categories("", 5) == {}


class TestParseMergedCategories:
    def test_basic_merge(self):
        original = {
            "Crack Detection A": [0, 1],
            "Crack Detection B": [2, 3],
            "SHM Sensors": [4, 5],
        }
        text = (
            "FINAL: Crack Detection\n"
            "MERGE: Crack Detection A, Crack Detection B\n\n"
            "FINAL: Sensor-Based SHM\n"
            "MERGE: SHM Sensors\n"
        )
        result = parse_merged_categories(text, original)
        assert result is not None
        assert "Crack Detection" in result
        assert sorted(result["Crack Detection"]) == [0, 1, 2, 3]
        assert result["Sensor-Based SHM"] == [4, 5]

    def test_returns_none_on_empty(self):
        assert parse_merged_categories("", {"A": [0]}) is None

    def test_returns_none_if_too_many_papers_lost(self):
        original = {"A": [0], "B": [1, 2, 3, 4, 5, 6, 7, 8, 9]}
        # Only merges A (1 paper), losing 9 of 10 (>50% lost)
        text = "FINAL: Group\nMERGE: A\n"
        result = parse_merged_categories(text, original)
        assert result is None

    def test_fuzzy_match(self):
        original = {"Vision-Based Crack Detection": [0, 1]}
        text = "FINAL: Vision\nMERGE: Vision-Based Crack Detection\n"
        result = parse_merged_categories(text, original)
        assert result is not None
        assert result["Vision"] == [0, 1]


class TestBuildTieredCorpus:
    def test_empty_papers(self):
        result = build_tiered_corpus([], token_budget=15000)
        assert result == ""

    def test_includes_abstracts_for_top_papers(self):
        indexed = [(i, Paper(title=f"Paper {i}", abstract="Abstract text here", citation_count=100-i)) for i in range(5)]
        result = build_tiered_corpus(indexed, token_budget=15000)
        assert "Abstract" in result

    def test_respects_budget(self):
        indexed = [(i, Paper(title=f"Paper {i}", abstract="x" * 500, citation_count=100-i)) for i in range(100)]
        result = build_tiered_corpus(indexed, token_budget=500)
        assert "additional papers" in result

    def test_uses_global_indices(self):
        """Paper [42] in category should show as [42], not [1]."""
        indexed = [(41, Paper(title="Test Paper", abstract="Test", citation_count=10))]
        result = build_tiered_corpus(indexed, token_budget=15000)
        assert "[42]" in result  # 0-based 41 -> 1-based 42


class TestTitlesMatch:
    def test_identical_titles(self):
        assert titles_match("Deep Learning for Image Recognition", "Deep Learning for Image Recognition")

    def test_different_punctuation(self):
        assert titles_match(
            "Deep Learning for Image Recognition: A Survey",
            "Deep Learning for Image Recognition A Survey",
        )

    def test_partial_overlap_above_threshold(self):
        assert titles_match(
            "Vision-Based Crack Detection in Concrete Structures",
            "Vision Based Crack Detection for Concrete",
        )

    def test_completely_different_titles(self):
        assert not titles_match(
            "Quantum Computing Applications in Cryptography",
            "Deep Learning for Natural Language Processing",
        )

    def test_empty_titles(self):
        assert not titles_match("", "Some Title")
        assert not titles_match("Some Title", "")
        assert not titles_match("", "")

    def test_only_stopwords(self):
        # After removing stopwords, both sets are empty -> False
        assert not titles_match("the a an", "the of in")

    def test_truncated_title(self):
        # Truncated version should still match
        assert titles_match(
            "Neural Networks for Structural Health Monitoring of Bridges",
            "Neural Networks for Structural Health Monitoring",
        )
