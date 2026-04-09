"""Tests for chart data extraction and SVG rendering."""
from __future__ import annotations

from deep_researcher.models import Paper


def _papers():
    return [
        Paper(title="A", year=2020, citation_count=10, source="scholar"),
        Paper(title="B", year=2021, citation_count=5, source="scholar,openalex"),
        Paper(title="C", year=2021, citation_count=3, source="openalex"),
        Paper(title="D", year=2024, citation_count=2, source="pubmed"),
        Paper(title="E", year=None, citation_count=0, source=""),
    ]


def test_compute_chart_data_years_zero_fill():
    from deep_researcher.charts import compute_chart_data
    papers = _papers()
    data = compute_chart_data(papers, {p.unique_key: p for p in papers}, None)
    assert data["years"] == {2020: 1, 2021: 2, 2022: 0, 2023: 0, 2024: 1}


def test_compute_chart_data_categories_sorted_desc():
    from deep_researcher.charts import compute_chart_data
    papers = _papers()
    cats = {"Small": [3], "Big": [0, 1, 2]}
    data = compute_chart_data(papers, {p.unique_key: p for p in papers}, cats)
    assert [c[0] for c in data["categories"]] == ["Big", "Small"]
    assert data["categories"][0] == ("Big", 3, 18)
    assert data["categories"][1] == ("Small", 1, 2)


def test_compute_chart_data_sources_comma_split():
    from deep_researcher.charts import compute_chart_data
    papers = _papers()
    data = compute_chart_data(papers, {p.unique_key: p for p in papers}, None)
    assert data["sources"]["scholar"] == 2
    assert data["sources"]["openalex"] == 2
    assert data["sources"]["pubmed"] == 1
    assert "" not in data["sources"]


def test_compute_chart_data_empty_corpus():
    from deep_researcher.charts import compute_chart_data
    data = compute_chart_data([], {}, None)
    assert data["years"] == {}
    assert data["categories"] == []
    assert data["sources"] == {}


def test_compute_chart_data_none_categories_produces_empty_list():
    from deep_researcher.charts import compute_chart_data
    papers = _papers()
    data = compute_chart_data(papers, {p.unique_key: p for p in papers}, None)
    assert data["categories"] == []


def test_render_year_histogram_valid_svg():
    from deep_researcher.charts import render_year_histogram
    svg = render_year_histogram({2020: 3, 2021: 1, 2022: 0, 2023: 2})
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert svg.count("<rect") >= 4
    assert "2020: 3 papers" in svg or "2020: 3 paper" in svg
    assert "viewBox" in svg


def test_render_year_histogram_empty():
    from deep_researcher.charts import render_year_histogram
    svg = render_year_histogram({})
    assert svg.startswith("<svg")
    assert "No data" in svg


def test_render_category_bars_valid_svg():
    from deep_researcher.charts import render_category_bars
    svg = render_category_bars([("Cat A", 5, 120), ("Cat B", 2, 40)])
    assert svg.startswith("<svg")
    assert svg.count("<rect") >= 4
    assert "Cat A" in svg
    assert "Cat B" in svg
    assert "120" in svg and "40" in svg


def test_render_category_bars_empty():
    from deep_researcher.charts import render_category_bars
    svg = render_category_bars([])
    assert svg.startswith("<svg")
    assert "No data" in svg


def test_render_source_donut_valid_svg():
    from deep_researcher.charts import render_source_donut
    svg = render_source_donut({"scholar": 40, "openalex": 30, "pubmed": 10})
    assert svg.startswith("<svg")
    assert svg.count("<circle") >= 3
    assert ">80<" in svg or "80" in svg
    assert "scholar" in svg
    assert "openalex" in svg


def test_render_source_donut_empty():
    from deep_researcher.charts import render_source_donut
    svg = render_source_donut({})
    assert svg.startswith("<svg")
    assert "No data" in svg


def test_render_all_charts_wraps_in_details():
    from deep_researcher.charts import render_all_charts
    data = {
        "years": {2020: 1},
        "categories": [("A", 1, 5)],
        "sources": {"scholar": 1},
    }
    html_out = render_all_charts(data)
    assert html_out.startswith("<details")
    assert "</details>" in html_out
    assert "At a glance" in html_out
    assert html_out.count("<svg") == 3


def test_render_year_histogram_escapes_xml():
    from deep_researcher.charts import render_year_histogram
    svg = render_year_histogram({2020: 5})
    import re
    bad = re.findall(r"&(?![a-zA-Z]+;|#\d+;)", svg)
    assert bad == []
