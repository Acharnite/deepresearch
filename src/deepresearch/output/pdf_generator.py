"""PDF generation for DeepResearch output.

Uses Jinja2 for HTML rendering and WeasyPrint for PDF conversion.
If WeasyPrint is unavailable, falls back to HTML-only output.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from deepresearch.models import AgentProfile, ResearchPaper

logger = logging.getLogger(__name__)

# Default template directory relative to this module.
DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

# Font-family mapping by language script.
_FONT_FAMILY_MAP: dict[str, str] = {
    # CJK
    "chinese": "'Noto Sans CJK SC', 'Microsoft YaHei', 'PingFang SC', sans-serif",
    "japanese": "'Noto Sans CJK JP', 'Hiragino Sans', 'Yu Gothic', sans-serif",
    "korean": "'Noto Sans CJK KR', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif",
    # RTL
    "arabic": "'Noto Sans Arabic', 'Geeza Pro', 'Traditional Arabic', sans-serif",
    "hebrew": "'Noto Sans Hebrew', 'Arial Hebrew', sans-serif",
    # Default (Western)
    "default": "'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
}

# Language to font-family lookup helper.
_LANGUAGE_FONT_MAP: dict[str, str] = {
    "danish": "'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
    "german": "'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
    "french": "'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
    "spanish": "'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif",
}


def _get_font_family(language: str | None = None) -> str:
    """Return the CSS font-family string for a given output language.

    Args:
        language: Output language name (e.g. "English", "Chinese (Simplified)").

    Returns:
        CSS font-family value.
    """
    if not language:
        return _FONT_FAMILY_MAP["default"]

    lang_lower = language.lower().strip()

    # Direct match on language name.
    if lang_lower in _FONT_FAMILY_MAP:
        return _FONT_FAMILY_MAP[lang_lower]

    # Check explicit language-to-font mapping.
    if lang_lower in _LANGUAGE_FONT_MAP:
        return _LANGUAGE_FONT_MAP[lang_lower]

    # Substring matching for compound names like "Chinese (Simplified)".
    if "chinese" in lang_lower:
        return _FONT_FAMILY_MAP["chinese"]
    if "japanese" in lang_lower:
        return _FONT_FAMILY_MAP["japanese"]
    if "korean" in lang_lower:
        return _FONT_FAMILY_MAP["korean"]
    if "arabic" in lang_lower:
        return _FONT_FAMILY_MAP["arabic"]
    if "hebrew" in lang_lower:
        return _FONT_FAMILY_MAP["hebrew"]

    return _FONT_FAMILY_MAP["default"]

# Color palette matching the dashboard agent colors.
# These cycle through 6 distinct colors for agent sections.
AGENT_COLORS = [
    "#58a6ff",  # blue   (Analyst)
    "#3fb950",  # green  (Researcher)
    "#f0883e",  # orange (Data Analyst)
    "#f85149",  # red    (Fact Checker)
    "#bc8cff",  # purple (Writer)
    "#39d2c0",  # teal   (Reviewer)
]

# Known role-to-CSS-class mapping (for agents matching standard roles).
_ROLE_CSS_MAP: dict[str, str] = {
    "analyst": "agent-analyst",
    "researcher": "agent-researcher",
    "data-analyst": "agent-data-analyst",
    "data analyst": "agent-data-analyst",
    "fact-checker": "agent-fact-checker",
    "fact checker": "agent-fact-checker",
    "writer": "agent-writer",
    "reviewer": "agent-reviewer",
}


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

    def render_html(
        self,
        paper: ResearchPaper,
        agent_profiles: list[AgentProfile] | None = None,
        budget_summary: str | None = None,
        language: str | None = None,
    ) -> str:
        """Render a ResearchPaper to a full HTML document via Jinja2.

        Args:
            paper: The compiled research paper.
            agent_profiles: Optional list of agent profiles for the cover page.
            budget_summary: Optional budget/methodology summary line.
            language: Output language for font selection (e.g. "English").

        Returns:
            Complete HTML string (including ``<html>``, ``<body>``, …).
        """
        template = self._env.get_template("paper.html")

        # Build agent color mapping.
        agent_colors = self._build_color_palette(agent_profiles)
        agent_css_classes = self._build_css_class_map(
            agent_profiles, paper
        )

        # Resolve font family for the output language.
        font_family = _get_font_family(language)

        # Read CSS file for inlining — WeasyPrint cannot resolve relative
        # <link> paths, so the CSS must be embedded directly in the HTML.
        css_content = ""
        css_path = self._template_dir / "styles.css"
        try:
            css_content = css_path.read_text(encoding="utf-8")
        except (OSError, IOError) as exc:
            logger.warning("Could not read styles.css for inlining: %s", exc)

        return template.render(
            paper=paper,
            generation_date=datetime.now().strftime("%B %d, %Y"),
            agent_profiles=agent_profiles or [],
            agent_colors=agent_colors,
            agent_css_classes=agent_css_classes,
            budget_summary=budget_summary,
            font_family=font_family,
            css_content=css_content,
            references=paper.references or [],
        )

    def generate_pdf(
        self,
        paper: ResearchPaper,
        output_path: str | Path,
        agent_profiles: list[AgentProfile] | None = None,
        budget_summary: str | None = None,
        language: str | None = None,
    ) -> Path:
        """Generate a PDF from a ResearchPaper.

        If WeasyPrint is not installed, falls back to writing an HTML
        file with a ``.html`` extension and logs a warning.

        Args:
            paper: The compiled research paper.
            output_path: Destination path for the PDF file.
            agent_profiles: Optional agent profiles for cover page.
            budget_summary: Optional budget/methodology summary line.
            language: Output language for font selection (e.g. "English").

        Returns:
            ``Path`` to the generated output file (PDF or HTML fallback).

        Raises:
            PDFGenerationError: If PDF generation fails for a reason
                other than a missing WeasyPrint installation.
        """
        output = Path(output_path)
        html = self.render_html(
            paper,
            agent_profiles=agent_profiles,
            budget_summary=budget_summary,
            language=language,
        )

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

    def generate_html_only(
        self,
        paper: ResearchPaper,
        agent_profiles: list[AgentProfile] | None = None,
        budget_summary: str | None = None,
        language: str | None = None,
    ) -> str:
        """Generate HTML without attempting PDF conversion.

        Useful for testing or previewing the generated layout.
        """
        return self.render_html(
            paper,
            agent_profiles=agent_profiles,
            budget_summary=budget_summary,
            language=language,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_color_palette(
        agent_profiles: list[AgentProfile] | None,
    ) -> list[str]:
        """Return a list of hex colors aligned with agent profiles.

        Colors cycle through the palette for agents beyond the first 6.
        """
        if not agent_profiles:
            return []
        return [
            AGENT_COLORS[i % len(AGENT_COLORS)]
            for i in range(len(agent_profiles))
        ]

    @staticmethod
    def _build_css_class_map(
        agent_profiles: list[AgentProfile] | None,
        paper: ResearchPaper,
    ) -> dict[str, str]:
        """Build a mapping from agent_id to CSS class name.

        Known role names get a dedicated class; unknown agents get a
        cycling fallback class.
        """
        # Map profile IDs to CSS classes.
        id_to_class: dict[str, str] = {}
        if agent_profiles:
            for i, profile in enumerate(agent_profiles):
                # Try matching by role name first.
                lower_name = profile.name.lower()
                css_class = _ROLE_CSS_MAP.get(lower_name)
                if not css_class:
                    # Try matching by profile ID.
                    css_class = _ROLE_CSS_MAP.get(profile.id.lower())
                if not css_class:
                    # Fallback: cycling color index.
                    css_class = f"agent-color-{i % len(AGENT_COLORS)}"
                id_to_class[profile.id] = css_class

        # Also build for any agent IDs found in sections that aren't in
        # profiles (defensive — should not happen in normal flow).
        seen_ids: set[str] = set()
        for section in paper.sections:
            if section.source_agent_id and section.source_agent_id not in id_to_class:
                idx = len(id_to_class)
                id_to_class[section.source_agent_id] = (
                    f"agent-color-{idx % len(AGENT_COLORS)}"
                )
            if section.source_agent_id:
                seen_ids.add(section.source_agent_id)

        return id_to_class

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
