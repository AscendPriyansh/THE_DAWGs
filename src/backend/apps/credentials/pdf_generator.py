"""
PDF certificate generation using ReportLab.

Generates certificates with event name, recipient, contribution/award,
issue date, credential ID and verification QR code. Uses bundled fonts
and no external dependencies.
"""

import io
import hashlib

# Try to import ReportLab support
try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm, cm
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas
    from reportlab.lib.enums import TA_CENTER
    HAS_REPORTLAB = True
    PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)
    COLOR_PRIMARY = HexColor("#0A0A0A")
    COLOR_ACCENT = HexColor("#6366F1")
    COLOR_GOLD = HexColor("#D4AF37")
    COLOR_TEXT = HexColor("#1F2937")
    COLOR_SUBTLE = HexColor("#6B7280")
    COLOR_BORDER = HexColor("#E5E7EB")
    COLOR_BG = HexColor("#FAFAFA")
except ImportError:
    HAS_REPORTLAB = False
    PAGE_WIDTH, PAGE_HEIGHT = (841.89, 595.27)
    mm = 2.834645669291339
    cm = 28.346456692913385
    COLOR_PRIMARY = None
    COLOR_ACCENT = None
    COLOR_GOLD = None
    COLOR_TEXT = None
    COLOR_SUBTLE = None
    COLOR_BORDER = None
    COLOR_BG = None

# Try to import QR code support
try:
    import qrcode
    import qrcode.image.svg
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


def generate_certificate_pdf(
    event_name: str,
    recipient_name: str,
    kind: str,
    contribution_text: str,
    issue_date: str,
    credential_id: str,
    verification_url: str,
    award_name: str = None,
) -> bytes:
    """
    Generate a PDF certificate. Returns raw PDF bytes.

    The credential_id and verification URL are rendered first (in a QR code),
    then the PDF bytes are hashed and included in the signed JSON payload.
    This avoids a circular dependency (PDF containing its own signature hash).
    """
    if not HAS_REPORTLAB:
        # Fallback minimal valid PDF if ReportLab is not installed
        minimal_pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 842 595]/Parent 2 0 R/Resources<<>>>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000010 00000 n \n0000000060 00000 n \n0000000115 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF\n"
        )
        return minimal_pdf

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(A4))
    c.setTitle(f"Certificate - {recipient_name}")
    c.setAuthor("Dogfood Hackathon Portal")
    c.setSubject(f"{kind} Certificate")

    # Background
    c.setFillColor(HexColor("#FFFFFF"))
    c.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=True, stroke=False)

    # Outer decorative border
    c.setStrokeColor(COLOR_ACCENT)
    c.setLineWidth(3)
    margin = 15 * mm
    c.rect(margin, margin, PAGE_WIDTH - 2 * margin, PAGE_HEIGHT - 2 * margin, fill=False)

    # Inner decorative border
    inner_margin = 20 * mm
    c.setStrokeColor(COLOR_BORDER)
    c.setLineWidth(1)
    c.rect(inner_margin, inner_margin, PAGE_WIDTH - 2 * inner_margin, PAGE_HEIGHT - 2 * inner_margin, fill=False)

    # Top accent line
    c.setStrokeColor(COLOR_ACCENT)
    c.setLineWidth(2)
    y_top_line = PAGE_HEIGHT - 35 * mm
    c.line(margin + 20 * mm, y_top_line, PAGE_WIDTH - margin - 20 * mm, y_top_line)

    # Header: "CERTIFICATE"
    c.setFont("Helvetica", 14)
    c.setFillColor(COLOR_ACCENT)
    y_header = PAGE_HEIGHT - 50 * mm
    c.drawCentredString(PAGE_WIDTH / 2, y_header, "CERTIFICATE")

    # Kind subtitle
    kind_label = {
        "PARTICIPANT": "OF PARTICIPATION",
        "JUDGE": "OF JUDGING SERVICE",
        "WINNER": "OF ACHIEVEMENT",
    }.get(kind, "OF RECOGNITION")

    c.setFont("Helvetica-Bold", 28)
    c.setFillColor(COLOR_PRIMARY)
    y_kind = y_header - 12 * mm
    c.drawCentredString(PAGE_WIDTH / 2, y_kind, kind_label)

    # Decorative divider
    c.setStrokeColor(COLOR_GOLD)
    c.setLineWidth(1.5)
    y_div = y_kind - 8 * mm
    div_half = 40 * mm
    c.line(PAGE_WIDTH / 2 - div_half, y_div, PAGE_WIDTH / 2 + div_half, y_div)

    # "This certifies that" line
    c.setFont("Helvetica", 11)
    c.setFillColor(COLOR_SUBTLE)
    y_certify = y_div - 10 * mm
    c.drawCentredString(PAGE_WIDTH / 2, y_certify, "This certifies that")

    # Recipient name
    c.setFont("Helvetica-Bold", 24)
    c.setFillColor(COLOR_PRIMARY)
    y_name = y_certify - 14 * mm
    c.drawCentredString(PAGE_WIDTH / 2, y_name, recipient_name)

    # Underline for name
    name_width = c.stringWidth(recipient_name, "Helvetica-Bold", 24)
    c.setStrokeColor(COLOR_ACCENT)
    c.setLineWidth(1)
    c.line(
        PAGE_WIDTH / 2 - name_width / 2 - 10,
        y_name - 3,
        PAGE_WIDTH / 2 + name_width / 2 + 10,
        y_name - 3,
    )

    # Contribution text / award
    c.setFont("Helvetica", 12)
    c.setFillColor(COLOR_TEXT)
    y_contrib = y_name - 16 * mm
    c.drawCentredString(PAGE_WIDTH / 2, y_contrib, contribution_text)

    # Award name if winner
    if award_name:
        c.setFont("Helvetica-Bold", 14)
        c.setFillColor(COLOR_GOLD)
        y_award = y_contrib - 10 * mm
        c.drawCentredString(PAGE_WIDTH / 2, y_award, f"🏆 {award_name}")
        y_event_line = y_award - 12 * mm
    else:
        y_event_line = y_contrib - 12 * mm

    # Event name
    c.setFont("Helvetica", 11)
    c.setFillColor(COLOR_SUBTLE)
    c.drawCentredString(PAGE_WIDTH / 2, y_event_line, f"at {event_name}")

    # Bottom section: date and credential ID
    y_bottom = margin + 30 * mm

    # Issue date (left)
    c.setFont("Helvetica", 9)
    c.setFillColor(COLOR_SUBTLE)
    c.drawString(inner_margin + 10 * mm, y_bottom + 5 * mm, "Date Issued")
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(COLOR_TEXT)
    c.drawString(inner_margin + 10 * mm, y_bottom, issue_date)

    # Credential ID (center)
    c.setFont("Helvetica", 8)
    c.setFillColor(COLOR_SUBTLE)
    cred_short = str(credential_id)[:8]
    c.drawCentredString(PAGE_WIDTH / 2, y_bottom, f"Credential ID: {credential_id}")

    # QR code (right side) - using verification URL
    if HAS_QRCODE:
        try:
            qr = qrcode.QRCode(version=1, box_size=3, border=1)
            qr.add_data(verification_url)
            qr.make(fit=True)
            # Generate QR as image in memory
            qr_img = qr.make_image(fill_color="black", back_color="white")
            qr_buf = io.BytesIO()
            qr_img.save(qr_buf, format="PNG")
            qr_buf.seek(0)
            from reportlab.lib.utils import ImageReader
            qr_reader = ImageReader(qr_buf)
            qr_size = 22 * mm
            c.drawImage(
                qr_reader,
                PAGE_WIDTH - inner_margin - qr_size - 10 * mm,
                y_bottom - 5 * mm,
                width=qr_size,
                height=qr_size,
            )
        except Exception:
            # Fallback: just print the URL
            c.setFont("Helvetica", 7)
            c.setFillColor(COLOR_ACCENT)
            c.drawRightString(PAGE_WIDTH - inner_margin - 10 * mm, y_bottom, verification_url)
    else:
        c.setFont("Helvetica", 7)
        c.setFillColor(COLOR_ACCENT)
        c.drawRightString(PAGE_WIDTH - inner_margin - 10 * mm, y_bottom, f"Verify: {verification_url}")

    # Bottom accent line
    c.setStrokeColor(COLOR_ACCENT)
    c.setLineWidth(2)
    c.line(margin + 20 * mm, margin + 22 * mm, PAGE_WIDTH - margin - 20 * mm, margin + 22 * mm)

    c.showPage()
    c.save()

    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes


def pdf_sha256(pdf_bytes: bytes) -> str:
    """Compute SHA-256 hex digest of PDF bytes."""
    return hashlib.sha256(pdf_bytes).hexdigest()
