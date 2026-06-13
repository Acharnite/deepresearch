"""PDF generation for DeepResearch output.

Uses Jinja2 for HTML rendering and WeasyPrint for PDF conversion.
If WeasyPrint is unavailable, falls back to HTML-only output.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from deepresearch.models import ResearchPaper

logger = logging.getLogger(__name__)

# Default template directory relative to this module.
DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


class PDFGenerationError(Exception):
    """Raised when PDF generation fails."""


class PDFGenerator:
    """Generate PDFs from ResearchPaper data using Jinja2 + WeasyPrint.

    Args:
        template_dir: Override path to template directory. Defaults to
            ``<module_dir>/templates/``.

    Usage::

        gen = PDFGenerator()
        paper = ResearchPaper(...)
        pdf_path = gen.generate_pdf(paper, "output/research.pdf")
    """

    def __init__(
        self,
        template_dir: str | Path | None = None,
    ) -> None:
        self._template_dir = Path(template_dir or DEFAULT_TEMPLATE_DIR)
        self._env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            autoescape=True,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_html(self, paper: ResearchPaper) -> str:
        """Render a ResearchPaper to a full HTML document via Jinja2.

        Args:
            paper: The compiled research paper.

        Returns:
            Complete HTML string (including ``<html>``, ``<body>``, …).
        """
        template = self._env.get_template("paper.html")
        return template.render(
            paper=paper,
            generation_date=datetime.now().strftime("%B %d, %Y"),
        )

    def generate_pdf(
        self,
        paper: ResearchPaper,
        output_path: str | Path,
    ) -> Path:
        """Generate a PDF from a ResearchPaper.

        If WeasyPrint is not installed, falls back to writing an HTML
        file with a ``.html`` extension and logs a warning.

        Args:
            paper: The compiled research paper.
            output_path: Destination path for the PDF file.

        Returns:
            ``Path`` to the generated output file (PDF or HTML fallback).

        Raises:
            PDFGenerationError: If PDF generation fails for a reason
                other than a missing WeasyPrint installation.
        """
        output = Path(output_path)
        html = self.render_html(paper)

        # Create parent directories if needed.
        output.parent.mkdir(parents=True, exist_ok=True)

        # Try WeasyPrint; fall back to HTML.
        pdf_success = self._try_weasyprint(html, output)

        if not pdf_success:
            # Fallback: write HTML file.
            html_output = output.with_suffix(".html")
            html_output.write_text(html, encoding="utf-8")
            logger.warning(
                "WeasyPrint unavailable — wrote HTML fallback to %s",
                html_output,
            )
            return html_output

        logger.info("PDF generated: %s", output)
        return output

    def generate_html_only(self, paper: ResearchPaper) -> str:
        """Generate HTML without attempting PDF conversion.

        Useful for testing or previewing the generated layout.
        """
        return self.render_html(paper)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_weasyprint(html: str, output: Path) -> bool:
        """Attempt to generate a PDF via WeasyPrint.

        Returns ``True`` on success, ``False`` if WeasyPrint is not
        installed. Raises ``PDFGenerationError`` on other failures.
        """
        try:
            from weasyprint import HTML as WeasyPrintHTML
        except ImportError:
            logger.debug("WeasyPrint is not installed — skipping PDF generation")
            return False

        try:
            WeasyPrintHTML(string=html).write_pdf(target=str(output))
            return True
        except Exception as exc:
            raise PDFGenerationError(
                f"WeasyPrint failed to generate PDF: {exc}"
            ) from exc
