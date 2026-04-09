"""Tests for output file versioning in save_report()."""
from __future__ import annotations

import os


def test_next_version_empty_folder(tmp_path):
    from deep_researcher.report import _next_version
    assert _next_version(str(tmp_path)) == 1


def test_next_version_with_v1(tmp_path):
    from deep_researcher.report import _next_version
    (tmp_path / "report.md").write_text("x")
    (tmp_path / "report.html").write_text("x")
    assert _next_version(str(tmp_path)) == 2


def test_next_version_with_v1_and_v2(tmp_path):
    from deep_researcher.report import _next_version
    (tmp_path / "report.md").write_text("x")
    (tmp_path / "report-2.md").write_text("x")
    assert _next_version(str(tmp_path)) == 3


def test_next_version_gap_fills(tmp_path):
    """If report.md + report-5.md exist but 2/3/4 are free, return 2."""
    from deep_researcher.report import _next_version
    (tmp_path / "report.md").write_text("x")
    (tmp_path / "report-5.md").write_text("x")
    assert _next_version(str(tmp_path)) == 2


def test_next_version_considers_html_slot(tmp_path):
    """If only report-2.html exists (no md), the next version is still >= 3."""
    from deep_researcher.report import _next_version
    (tmp_path / "report.md").write_text("x")
    (tmp_path / "report-2.html").write_text("x")
    assert _next_version(str(tmp_path)) == 3


def test_save_report_versioned_on_replay(tmp_path):
    """Running save_report twice into the same folder produces report-2.md."""
    from deep_researcher.models import Paper
    from deep_researcher.report import save_report
    papers = {
        "k1": Paper(title="One", year=2023, citation_count=1, source="scholar"),
    }
    folder = str(tmp_path)
    paths1 = save_report("q", "### q\ntext", papers, folder, folder=folder,
                         synthesis_papers=list(papers.values()))
    assert paths1["report"].endswith("report.md")
    assert paths1["html"].endswith("report.html")

    paths2 = save_report("q", "### q\ntext v2", papers, folder, folder=folder,
                         synthesis_papers=list(papers.values()))
    assert paths2["report"].endswith("report-2.md")
    assert paths2["html"].endswith("report-2.html")
    # Original files untouched
    with open(os.path.join(folder, "report.md")) as f:
        assert "text v2" not in f.read()
