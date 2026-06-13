"""Tests for the PDFGenerator (Phase 5).

Covers:
  - Jinja2 template renders all sections correctly
  - CSS stylesheet is valid
  - PDFGenerator renders HTML from ResearchPaper
  - WeasyPrint generates actual PDF file (if available)
  - Multiple sections render correctly
  - Empty/edge cases (no sections, no takeaways)
  - Output path creation (nested directories)
  - HTML-only fallback when WeasyPrint is unavailable
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch.models import PaperSection, ResearchPaper
from deepresearch.output.pdf_generator import PDFGenerator


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_paper() -> ResearchPaper:
    """A fully populated ResearchPaper for template testing."""
    return ResearchPaper(
        title="Quantum Computing in Healthcare: A Multi-Perspective Analysis",
        abstract=(
            "This paper explores the impact of quantum computing on healthcare "
            "through multiple analytical lenses. Each agent brings a unique "
            "perspective to bear on the topic."
        ),
        methodology_note=(
            "Multi-agent collaborative research methodology. Three agents with "
            "distinct analytical approaches conducted independent research, "
            "shared findings, and refined their analyses collaboratively."
        ),
        sections=[
            PaperSection(
                heading="Introduction",
                source_agent_id=None,
                content=(
                    "Quantum computing promises to revolutionise healthcare "
                    "by enabling simulations that are infeasible on classical "
                    "computers. This paper examines the topic from scientific, "
                    "ethical, and practical perspectives."
                ),
                subsections=[
                    PaperSection(
                        heading="Background",
                        source_agent_id=None,
                        content="Classical computing limitations in drug discovery.",
                        subsections=[],
                    ),
                ],
            ),
            PaperSection(
                heading="Scientific Perspective",
                source_agent_id="agent-alpha",
                content=(
                    "From a scientific standpoint, quantum computing offers "
                    "exponential speedups for molecular simulation."
                ),
                subsections=[],
            ),
        ],
        synthesis=(
            "Across all perspectives, several key themes emerge. The scientific "
            "potential is clear, but practical timelines remain uncertain."
        ),
        key_takeaways=[
            "Quantum computing can significantly accelerate drug discovery",
            "Ethical frameworks must be developed in parallel with the technology",
            "Interdisciplinary collaboration is essential for progress",
            "Near-term quantum advantage in healthcare remains an open question",
        ],
        conclusion=(
            "Quantum computing holds transformative potential for healthcare, "
            "but realising this potential requires sustained investment in "
            "hardware, algorithms, and ethical frameworks."
        ),
        appendices=[
            PaperSection(
                heading="Appendix A: Glossary",
                source_agent_id=None,
                content="Qubit: The basic unit of quantum information.",
                subsections=[],
            ),
        ],
    )


@pytest.fixture
def minimal_paper() -> ResearchPaper:
    """A minimal paper with only required fields."""
    return ResearchPaper(
        title="Minimal Paper",
        abstract="A minimal abstract.",
        methodology_note="Minimal methodology.",
        sections=[],
        synthesis="Minimal synthesis.",
        key_takeaways=[],
        conclusion="Minimal conclusion.",
    )


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestPDFGeneratorInit:
    """PDFGenerator initialisation."""

    def test_default_template_dir(self):
        """Default template directory should point to the templates folder."""
        gen = PDFGenerator()
        # Normalise for comparison.
        assert gen._template_dir.resolve().name == "templates"

    def test_custom_template_dir(self, tmp_path):
        """Custom template directory should be accepted."""
        gen = PDFGenerator(template_dir=tmp_path)
        assert gen._template_dir == tmp_path


class TestRenderHTML:
    """HTML rendering from ResearchPaper data."""

    def test_renders_title(self, sample_paper):
        """Title should appear in rendered HTML."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "Quantum Computing in Healthcare" in html

    def test_renders_abstract(self, sample_paper):
        """Abstract should appear in rendered HTML."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "impact of quantum computing on healthcare" in html

    def test_renders_sections(self, sample_paper):
        """All agent sections should appear."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "Introduction" in html
        assert "Scientific Perspective" in html

    def test_renders_subsections(self, sample_paper):
        """Subsections should appear nested under their parent."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "Background" in html

    def test_renders_source_attribution(self, sample_paper):
        """Source agent attribution should appear."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "agent-alpha" in html

    def test_renders_synthesis(self, sample_paper):
        """Synthesis section should appear."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "Across all perspectives" in html

    def test_renders_takeaways(self, sample_paper):
        """Key takeaways should appear as list items."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "exponentially" not in html  # not in takeaways
        assert "drug discovery" in html

    def test_renders_conclusion(self, sample_paper):
        """Conclusion should appear."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "transformative potential" in html

    def test_renders_appendices(self, sample_paper):
        """Appendices should appear."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert "Appendix A: Glossary" in html

    def test_empty_sections(self, minimal_paper):
        """Paper with no sections should still render valid HTML."""
        gen = PDFGenerator()
        html = gen.render_html(minimal_paper)
        assert "<html" in html
        assert "Minimal Paper" in html

    def test_empty_takeaways(self, minimal_paper):
        """Paper with no takeaways should still render."""
        gen = PDFGenerator()
        html = gen.render_html(minimal_paper)
        assert "Key Takeaways" in html
        assert "</html>" in html

    def test_date_format(self, sample_paper):
        """Generation date should be rendered."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        # Should contain month name (e.g., "June") and year.
        import datetime
        expected_month = datetime.datetime.now().strftime("%B")
        assert expected_month in html

    def test_valid_html_structure(self, sample_paper):
        """Generated HTML should have basic valid structure."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert html.startswith("<!DOCTYPE html>")
        assert "<html" in html
        assert "</html>" in html
        assert "<head>" in html
        assert "<body>" in html

    def test_generation_date_in_cover(self, sample_paper):
        """Cover page should contain the generation date."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        # The cover has a class "cover-date".
        assert 'class="cover-date"' in html


class TestCSS:
    """CSS stylesheet validity."""

    def test_stylesheet_exists(self):
        """CSS file should exist in template directory."""
        gen = PDFGenerator()
        css_path = gen._template_dir / "styles.css"
        assert css_path.exists()
        assert css_path.stat().st_size > 0

    def test_stylesheet_content(self):
        """Stylesheet should contain expected CSS rules."""
        gen = PDFGenerator()
        css_path = gen._template_dir / "styles.css"
        css = css_path.read_text(encoding="utf-8")

        # Core page/layout rules.
        assert "@page" in css
        assert "body {" in css
        assert ".cover-page {" in css
        assert ".abstract-section {" in css
        assert ".toc-section {" in css

        # Typography rules.
        assert "font-family" in css
        assert "line-height" in css

        # Section styling.
        assert ".section {" in css
        assert ".source-note {" in css

        # Special sections.
        assert ".synthesis-section" in css
        assert ".takeaways-section" in css

        # Code/blockquote formatting.
        assert "blockquote" in css
        assert "code" in css
        assert "pre" in css

        # Page break directives.
        assert "page-break" in css

    def test_stylesheet_links_from_html(self, sample_paper):
        """HTML should link to the stylesheet."""
        gen = PDFGenerator()
        html = gen.render_html(sample_paper)
        assert 'href="styles.css"' in html or 'href=styles.css' in html


class TestGeneratePDF:
    """PDF generation (with WeasyPrint fallback)."""

    def test_generate_pdf_creates_file(self, sample_paper, tmp_path):
        """generate_pdf should create a file at the output path."""
        gen = PDFGenerator()
        output = tmp_path / "test_output.pdf"
        result = gen.generate_pdf(sample_paper, output)

        assert result.exists()
        assert result.suffix in (".pdf", ".html")

    def test_generate_pdf_returns_path(self, sample_paper, tmp_path):
        """generate_pdf should return a Path."""
        gen = PDFGenerator()
        output = tmp_path / "return_test.pdf"
        result = gen.generate_pdf(sample_paper, output)

        assert isinstance(result, Path)

    def test_generate_pdf_fallback_html(self, sample_paper, tmp_path):
        """If WeasyPrint is unavailable, generate_pdf should write an HTML file."""
        gen = PDFGenerator()
        output = tmp_path / "fallback_test.pdf"
        result = gen.generate_pdf(sample_paper, output)

        assert result.exists()
        if result.suffix == ".html":
            # Fallback HTML path.
            content = result.read_text(encoding="utf-8")
            assert "Quantum Computing" in content
        else:
            # WeasyPrint available — real PDF was produced.
            assert result.suffix == ".pdf"
            with open(result, "rb") as f:
                header = f.read(5)
            assert header == b"%PDF-"

    def test_generate_pdf_nested_directory(self, sample_paper, tmp_path):
        """generate_pdf should create nested directories if needed."""
        gen = PDFGenerator()
        output = tmp_path / "nested" / "deep" / "report.pdf"
        result = gen.generate_pdf(sample_paper, output)

        assert result.exists()
        assert result.parent == tmp_path / "nested" / "deep"

    def test_generate_pdf_file_has_content(self, sample_paper, tmp_path):
        """Generated file should not be empty."""
        gen = PDFGenerator()
        output = tmp_path / "non_empty.pdf"
        result = gen.generate_pdf(sample_paper, output)

        assert result.stat().st_size > 0

    def test_generate_pdf_actual_pdf(self, sample_paper, tmp_path):
        """If WeasyPrint is available, the file should have a PDF header."""
        gen = PDFGenerator()
        output = tmp_path / "header_check.pdf"
        result = gen.generate_pdf(sample_paper, output)

        if result.suffix == ".pdf":
            with open(result, "rb") as f:
                header = f.read(5)
            assert header == b"%PDF-"
        else:
            # Fallback HTML — check it contains valid HTML.
            content = result.read_text(encoding="utf-8")
            assert "<html" in content

    def test_generate_html_only(self, sample_paper):
        """generate_html_only should return HTML string, no file written."""
        gen = PDFGenerator()
        html = gen.generate_html_only(sample_paper)

        assert isinstance(html, str)
        assert "<html" in html
        assert "Quantum Computing" in html

    def test_generate_pdf_multiple_sections(self, tmp_path):
        """Paper with many sections should render correctly."""
        sections = [
            PaperSection(
                heading=f"Section {i}",
                source_agent_id=f"agent-{i}" if i % 2 == 0 else None,
                content=f"Content for section {i}.",
                subsections=[
                    PaperSection(
                        heading=f"Sub {i}.{j}",
                        source_agent_id=None,
                        content=f"Subcontent {i}.{j}.",
                        subsections=[],
                    )
                    for j in range(2)
                ],
            )
            for i in range(5)
        ]
        paper = ResearchPaper(
            title="Multi-Section Paper",
            abstract="A paper with many sections.",
            methodology_note="Test.",
            sections=sections,
            synthesis="Synthesis of many sections.",
            key_takeaways=[f"Takeaway {i}" for i in range(5)],
            conclusion="Conclusion.",
        )
        gen = PDFGenerator()
        output = tmp_path / "multi_section.pdf"
        result = gen.generate_pdf(paper, output)

        assert result.exists()
        assert result.stat().st_size > 0

    def test_generate_pdf_minimal(self, minimal_paper, tmp_path):
        """Minimal paper with empty fields should still produce output."""
        gen = PDFGenerator()
        output = tmp_path / "minimal.pdf"
        result = gen.generate_pdf(minimal_paper, output)

        assert result.exists()
        content = result.read_text(encoding="utf-8") if result.suffix == ".html" else ""
        if result.suffix == ".html":
            assert "Minimal Paper" in content
