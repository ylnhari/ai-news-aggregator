"""PDF export functionality for AI news reports."""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib import colors
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
import logging
import re

logger = logging.getLogger(__name__)


def _md_to_rl(text: str) -> str:
    """Escape text for ReportLab Paragraph XML, then re-apply simple markdown.

    Without escaping, any '&' or '<' in scraped content crashes doc.build().
    Supports **bold**, *italic*, `code`, and [label](url) links.
    """
    text = escape(text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                  r'<link href="\2" color="#1DA1F2"><u>\1</u></link>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', text)
    # bare URLs -> clickable
    text = re.sub(r'(?<!href=")(?<!>)(https?://[^\s<]+)',
                  r'<link href="\1" color="#1DA1F2"><u>\1</u></link>', text)
    return text


class PDFExporter:
    """Export reports to PDF format."""

    def __init__(self, config):
        """Initialize PDF exporter."""
        self.config = config
        self.styles = self._create_styles()

    def _create_styles(self):
        """Create custom paragraph styles."""
        styles = getSampleStyleSheet()
        
        # Custom title style
        styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=styles['Heading1'],
            fontSize=24,
            textColor=colors.HexColor('#1DA1F2'),  # Twitter blue
            spaceAfter=6,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ))
        
        # Custom heading style
        styles.add(ParagraphStyle(
            name='CustomHeading',
            parent=styles['Heading2'],
            fontSize=14,
            textColor=colors.HexColor('#1DA1F2'),
            spaceAfter=12,
            spaceBefore=12,
            fontName='Helvetica-Bold'
        ))
        
        # Body text style
        styles.add(ParagraphStyle(
            name='CustomBody',
            parent=styles['BodyText'],
            fontSize=11,
            alignment=TA_JUSTIFY,
            spaceAfter=12
        ))
        
        # Tweet quote style
        styles.add(ParagraphStyle(
            name='TweetQuote',
            parent=styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#657786'),
            leftIndent=20,
            spaceAfter=6,
            fontName='Courier'
        ))
        
        return styles

    def export_report_to_pdf(
        self,
        report_content: str,
        output_filename: str = None,
        metadata: dict = None
    ) -> Path:
        """
        Export a report to PDF.
        
        Args:
            report_content: The report content (markdown or plain text)
            output_filename: Optional output filename
            metadata: Optional metadata (title, author, etc.)
            
        Returns:
            Path to the generated PDF
        """
        if output_filename is None:
            timestamp = datetime.now().strftime(
                self.config.get_setting("output", "timestamp_format", default="%Y%m%d_%H%M%S")
            )
            filename = f"{self.config.get_setting('output', 'filename_prefix', default='ai_news_digest')}_{timestamp}.pdf"
            output_filename = filename
        
        output_path = Path(self.config.output_dir) / output_filename
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Exporting report to PDF: {output_path}")
        
        # Create PDF document
        page_size = A4
        margins = self.config.get_setting("pdf", "margins", default=72)
        
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=page_size,
            rightMargin=margins,
            leftMargin=margins,
            topMargin=margins,
            bottomMargin=margins,
            title=metadata.get("title", "AI News Digest") if metadata else "AI News Digest"
        )
        
        # Build content
        story = self._build_pdf_content(report_content, metadata)
        
        # Generate PDF
        doc.build(story)
        logger.info(f"PDF exported successfully: {output_path}")
        
        return output_path

    def _build_pdf_content(self, report_content: str, metadata: dict = None):
        """Build the PDF content elements."""
        story = []
        
        # Add header
        title = "AI News Daily Digest"
        if metadata and "title" in metadata:
            title = metadata["title"]
        
        story.append(Paragraph(title, self.styles['CustomTitle']))
        
        # Add timestamp
        if self.config.get_setting("pdf", "include_timestamp", default=True):
            timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p")
            story.append(Paragraph(
                f"<font size=10 color='#657786'>Generated: {timestamp}</font>",
                self.styles['CustomHeading']
            ))
        
        story.append(Spacer(1, 0.3 * inch))
        
        # Parse and add report content
        sections = self._parse_report_content(report_content)
        
        for section in sections:
            if section['type'] == 'heading':
                story.append(Paragraph(_md_to_rl(section['text']), self.styles['CustomHeading']))
            elif section['type'] == 'body':
                story.append(Paragraph(_md_to_rl(section['text']), self.styles['CustomBody']))
            elif section['type'] == 'bullet':
                story.append(Paragraph(_md_to_rl(section['text']), self.styles['CustomBody'],
                                       bulletText='•'))
            elif section['type'] == 'quote':
                story.append(Paragraph(_md_to_rl(section['text']), self.styles['TweetQuote']))
            elif section['type'] == 'space':
                story.append(Spacer(1, section['height']))
        
        return story

    def _parse_report_content(self, content: str):
        """Parse report content into structured sections."""
        sections = []
        lines = content.split('\n')
        
        current_paragraph = ""
        
        for line in lines:
            line = line.strip()
            
            if not line:
                if current_paragraph:
                    sections.append({
                        'type': 'body',
                        'text': current_paragraph.strip()
                    })
                    current_paragraph = ""
                sections.append({'type': 'space', 'height': 0.1 * inch})
                continue
            
            # Detect headings
            if line.startswith('##'):
                if current_paragraph:
                    sections.append({
                        'type': 'body',
                        'text': current_paragraph.strip()
                    })
                    current_paragraph = ""
                
                heading_text = line.replace('##', '').replace('#', '').strip()
                sections.append({
                    'type': 'heading',
                    'text': heading_text
                })
            elif line.startswith('#'):
                if current_paragraph:
                    sections.append({
                        'type': 'body',
                        'text': current_paragraph.strip()
                    })
                    current_paragraph = ""
                
                heading_text = line.replace('#', '').strip()
                sections.append({
                    'type': 'heading',
                    'text': heading_text
                })
            elif line.startswith(('- ', '* ', '+ ')) or re.match(r'^\d+\.\s', line):
                if current_paragraph:
                    sections.append({
                        'type': 'body',
                        'text': current_paragraph.strip()
                    })
                    current_paragraph = ""
                bullet_text = re.sub(r'^(?:[-*+]|\d+\.)\s+', '', line)
                sections.append({
                    'type': 'bullet',
                    'text': bullet_text
                })
            else:
                current_paragraph += ' ' + line if current_paragraph else line
        
        if current_paragraph:
            sections.append({
                'type': 'body',
                'text': current_paragraph.strip()
            })

        return sections
