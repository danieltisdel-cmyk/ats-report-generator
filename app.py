import streamlit as st
import io
import json
from datetime import date
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, KeepTogether, Frame, PageTemplate, BaseDocTemplate
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas
from PIL import Image as PILImage
import tempfile
import os

# Page config
st.set_page_config(page_title="ATS Inspection Report Generator", layout="wide", page_icon="📋")

# Password gate
def expected_password():
    try:
        return st.secrets["password"]
    except Exception:
        return "ats2026"


def check_password():
    def password_entered():
        if st.session_state["password"] == expected_password():
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Enter team password", type="password", on_change=password_entered, key="password")
        st.info("Default password is `ats2026` (change it in Streamlit secrets for production).")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Enter team password", type="password", on_change=password_entered, key="password")
        st.error("Password incorrect")
        return False
    else:
        return True

if not check_password():
    st.stop()

st.markdown("""
<style>
    .main-header { color: #1a365d; font-weight: 600; }
    .stButton>button { background-color: #1a365d; color: white; }
</style>
""", unsafe_allow_html=True)

st.markdown('<h1 class="main-header">ATS Inspection Report Generator</h1>', unsafe_allow_html=True)
st.markdown("**Applied Technical Services** • Fully customizable for different customers")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOGO = os.path.join(SCRIPT_DIR, "ats_logo.jpg")


def ensure_default_logo():
    """Write the bundled ATS logo if it is not already on disk."""
    if os.path.exists(DEFAULT_LOGO):
        return DEFAULT_LOGO
    try:
        import base64
        from logo_data import LOGO_B64
        with open(DEFAULT_LOGO, "wb") as f:
            f.write(base64.b64decode(LOGO_B64))
    except Exception:
        return None
    return DEFAULT_LOGO if os.path.exists(DEFAULT_LOGO) else None


ensure_default_logo()

# Initialize session state for presets
if "presets" not in st.session_state:
    st.session_state.presets = {}

# Sidebar - Company + Presets
with st.sidebar:
    st.header("Company Settings")
    company_name = st.text_input("Company Name", "Applied Technical Services")
    company_address = st.text_area("Company Address", "214 Pelham Davis Circle\nGreenville, SC 29615\nwww.atslab.com")
    footer_text = st.text_input("Footer Text", "Confidential")
    logo_file = st.file_uploader("Company logo (optional override)", type=["png", "jpg", "jpeg"], key="co_logo")
    if logo_file:
        logo_path = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name
        with open(logo_path, "wb") as f:
            f.write(logo_file.getvalue())
    else:
        logo_path = DEFAULT_LOGO if os.path.exists(DEFAULT_LOGO) else None

    st.markdown("---")
    st.header("Customer Presets")
    preset_name = st.text_input("Preset name (to save)")
    if st.button("Save current as preset") and preset_name:
        st.session_state.presets[preset_name] = {
            "client_name": st.session_state.get("client_name", ""),
            "site_location": st.session_state.get("site_location", ""),
            "inspection_title": st.session_state.get("inspection_title", ""),
        }
        st.success(f"Saved preset: {preset_name}")
    if st.session_state.presets:
        selected = st.selectbox("Load preset", [""] + list(st.session_state.presets.keys()))
        if selected and st.button("Load preset"):
            p = st.session_state.presets[selected]
            st.session_state.client_name = p.get("client_name", "")
            st.session_state.site_location = p.get("site_location", "")
            st.session_state.inspection_title = p.get("inspection_title", "")
            st.rerun()

    st.markdown("---")
    st.caption("ATS Report Generator")

# Main form
st.subheader("Report Information")
col1, col2 = st.columns(2)

with col1:
    client_name = st.text_input("Client Name", "AEP", key="client_name")
    site_location = st.text_input("Site / Location", "510 N Allen Ave", key="site_location")
    inspection_title = st.text_input("Inspection Title", "Borescope of Attemperators", key="inspection_title")
    job_number = st.text_input("Job / Unit / Project Number", "")
    insp_date = st.date_input("Inspection Date", value=date.today())

with col2:
    prepared_by = st.text_input("Prepared By", "Daniel Tisdel")
    signoff_name = st.text_input("Signoff Name", "Daniel B Tisdel")
    scope = st.text_area("Scope", "Borescope of Attemperators", height=80)
    access = st.text_area("Access", "Through nozzles that were removed.", height=80)

findings = st.text_area("Findings", "Nozzles looked good...", height=120)

# Customization options
st.markdown("---")
st.subheader("Customization Options")
c1, c2, c3 = st.columns(3)
with c1:
    show_summary = st.checkbox("Include Inspection Summary", value=True)
    show_signoff = st.checkbox("Include Signoff Box", value=True)
with c2:
    accent_color = st.color_picker("Accent Color", "#1a365d")
    include_client_logo = st.checkbox("Add client logo on cover", value=False)
with c3:
    images_per_page = st.selectbox("Images per page", [1, 2], index=1)
    custom_cover_note = st.text_input("Extra cover note (optional)", "")

client_logo_path = None
if include_client_logo:
    client_logo_file = st.file_uploader("Client logo", type=["png", "jpg", "jpeg"], key="client_logo")
    if client_logo_file:
        client_logo_path = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name
        with open(client_logo_path, "wb") as f:
            f.write(client_logo_file.getvalue())

st.markdown("---")
st.subheader("Images")

st.markdown("**Standard Inspection Images**")
uploaded_images = st.file_uploader(
    "Upload images (JPG/PNG from Mentor MVIQ, phone, or other sources)",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True,
    key="std_images"
)

image_metadata = []
if uploaded_images:
    st.info(f"{len(uploaded_images)} image(s) uploaded. Expand each to add labels and comments.")
    for i, img_file in enumerate(uploaded_images):
        with st.expander(f"Image {i+1}: {img_file.name}", expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                label1 = st.text_input("Label 1", value="Component", key=f"l1_{i}")
                label2 = st.text_input("Label 2", value="", key=f"l2_{i}")
            with c2:
                comments = st.text_input("Comments", value="", key=f"c_{i}")
            image_metadata.append({
                "file": img_file,
                "name": img_file.name,
                "label1": label1,
                "label2": label2,
                "comments": comments,
            })

st.markdown("**Before / After Image Pairs** (optional)")
include_ba = st.checkbox("Include before/after pairs (displayed side-by-side)")
ba_pairs = []
if include_ba:
    num_pairs = st.number_input("Number of before/after pairs", min_value=1, max_value=10, value=1)
    for p in range(num_pairs):
        st.markdown(f"**Pair {p+1}**")
        bc1, bc2 = st.columns(2)
        with bc1:
            before_file = st.file_uploader(f"Before image {p+1}", type=["jpg", "jpeg", "png"], key=f"before_{p}")
        with bc2:
            after_file = st.file_uploader(f"After image {p+1}", type=["jpg", "jpeg", "png"], key=f"after_{p}")
        if before_file and after_file:
            pair_label = st.text_input(f"Pair {p+1} label / description", value=f"Before/After Pair {p+1}", key=f"pair_label_{p}")
            pair_comments = st.text_input(f"Pair {p+1} comments", value="", key=f"pair_c_{p}")
            ba_pairs.append({
                "before": before_file,
                "after": after_file,
                "label": pair_label,
                "comments": pair_comments
            })

# Generate PDF
if st.button("Generate Customized Report PDF", type="primary"):
    if not client_name or not inspection_title:
        st.error("Please fill in at least Client Name and Inspection Title.")
    else:
        with st.spinner("Generating customized report..."):
            buffer = io.BytesIO()
            accent = colors.HexColor(accent_color)

            class ProfessionalDocTemplate(BaseDocTemplate):
                def __init__(self, filename, **kwargs):
                    BaseDocTemplate.__init__(self, filename, **kwargs)
                    frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id='normal')
                    template = PageTemplate(id='main', frames=frame, onPage=self.add_page_decorations)
                    self.addPageTemplates([template])

                def add_page_decorations(self, canvas, doc):
                    canvas.saveState()
                    if doc.page > 1:
                        canvas.setStrokeColor(accent)
                        canvas.setLineWidth(1.5)
                        canvas.line(0.6*inch, letter[1] - 0.45*inch, letter[0] - 0.6*inch, letter[1] - 0.45*inch)
                        if logo_path and os.path.exists(logo_path):
                            try:
                                canvas.drawImage(logo_path, 0.6*inch, letter[1] - 0.42*inch, width=1.4*inch, height=0.35*inch, preserveAspectRatio=True, mask='auto')
                            except:
                                pass
                        canvas.setFont("Helvetica", 8)
                        canvas.setFillColor(colors.HexColor("#4a5568"))
                        canvas.drawRightString(letter[0] - 0.6*inch, letter[1] - 0.38*inch, company_name)

                    canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
                    canvas.setLineWidth(0.5)
                    canvas.line(0.6*inch, 0.45*inch, letter[0] - 0.6*inch, 0.45*inch)
                    canvas.setFont("Helvetica", 8)
                    canvas.setFillColor(colors.HexColor("#718096"))
                    canvas.drawString(0.6*inch, 0.28*inch, f"{company_name} • {footer_text}")
                    canvas.drawRightString(letter[0] - 0.6*inch, 0.28*inch, f"Page {doc.page}")
                    canvas.restoreState()

            doc = ProfessionalDocTemplate(
                buffer,
                pagesize=letter,
                rightMargin=0.6*inch,
                leftMargin=0.6*inch,
                topMargin=0.75*inch,
                bottomMargin=0.6*inch
            )

            styles = getSampleStyleSheet()
            styles.add(ParagraphStyle(name="CoverTitle", parent=styles["Title"], alignment=TA_CENTER, fontSize=16, textColor=accent, spaceAfter=8))
            styles.add(ParagraphStyle(name="CoverSub", parent=styles["Normal"], alignment=TA_CENTER, fontSize=11, textColor=colors.HexColor("#2d3748"), spaceAfter=4))
            styles.add(ParagraphStyle(name="SectionHead", parent=styles["Heading2"], fontSize=12, textColor=accent, spaceBefore=14, spaceAfter=6))
            styles.add(ParagraphStyle(name="BodyJust", parent=styles["Normal"], alignment=TA_LEFT, fontSize=10, leading=14, textColor=colors.HexColor("#2d3748")))
            styles.add(ParagraphStyle(name="SmallCenter", parent=styles["Normal"], alignment=TA_CENTER, fontSize=9, textColor=colors.HexColor("#4a5568")))

            story = []

            # Cover
            story.append(Spacer(1, 0.3*inch))
            if logo_path and os.path.exists(logo_path):
                try:
                    logo = Image(logo_path, width=3.0*inch, height=0.95*inch)
                    logo.hAlign = "CENTER"
                    story.append(logo)
                except Exception:
                    story.append(Paragraph(company_name, styles["CoverTitle"]))
            else:
                story.append(Paragraph(company_name, styles["CoverTitle"]))

            if client_logo_path and os.path.exists(client_logo_path):
                story.append(Spacer(1, 0.2*inch))
                try:
                    clogo = Image(client_logo_path, width=1.8*inch, height=0.7*inch)
                    clogo.hAlign = "CENTER"
                    story.append(clogo)
                except:
                    pass

            story.append(Spacer(1, 0.15*inch))
            story.append(Paragraph(company_name, styles["CoverSub"]))
            story.append(Spacer(1, 0.5*inch))

            story.append(Paragraph(f"<b>{client_name}</b>", styles["CoverTitle"]))
            story.append(Paragraph(site_location, styles["CoverSub"]))
            if job_number:
                story.append(Paragraph(f"Job / Unit: {job_number}", styles["CoverSub"]))
            story.append(Spacer(1, 0.1*inch))
            story.append(Paragraph(inspection_title, styles["CoverTitle"]))
            story.append(Paragraph(insp_date.strftime("%B %d, %Y"), styles["CoverSub"]))
            if custom_cover_note:
                story.append(Spacer(1, 0.1*inch))
                story.append(Paragraph(custom_cover_note, styles["CoverSub"]))
            story.append(Spacer(1, 0.6*inch))
            story.append(Paragraph(f"<b>Prepared By:</b><br/>{prepared_by}", styles["CoverSub"]))
            story.append(Spacer(1, 0.4*inch))
            story.append(Paragraph(company_name, styles["SmallCenter"]))
            story.append(Paragraph(company_address.replace("\n", "<br/>"), styles["SmallCenter"]))
            story.append(PageBreak())

            # Boxed sections
            def boxed_section(title, content):
                data = [[Paragraph(f"<b>{title}</b>", styles["SectionHead"]), ""],
                        [Paragraph(content, styles["BodyJust"]), ""]]
                t = Table(data, colWidths=[6.3*inch])
                t.setStyle(TableStyle([
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e0")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("SPAN", (0, 0), (-1, 0)),
                    ("SPAN", (0, 1), (-1, 1)),
                ]))
                return t

            story.append(boxed_section("Scope", scope))
            story.append(Spacer(1, 0.12*inch))
            story.append(boxed_section("Access", access))
            story.append(Spacer(1, 0.12*inch))
            story.append(boxed_section("Findings", findings))
            story.append(Spacer(1, 0.2*inch))

            if show_signoff:
                signoff_data = [[Paragraph("<b>Signoff</b>", styles["SectionHead"])],
                                [Paragraph(signoff_name, styles["BodyJust"])]]
                signoff_t = Table(signoff_data, colWidths=[3*inch])
                signoff_t.setStyle(TableStyle([
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e0")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]))
                story.append(signoff_t)
            story.append(PageBreak())

            # Summary
            if show_summary:
                total_std = len(image_metadata)
                total_ba = len(ba_pairs)
                summary_data = [
                    [Paragraph("<b>Inspection Summary</b>", styles["SectionHead"]), "", ""],
                    [Paragraph("<b>Category</b>", styles["BodyJust"]), Paragraph("<b>Count</b>", styles["BodyJust"]), Paragraph("<b>Notes</b>", styles["BodyJust"])],
                    ["Standard Images", str(total_std), "See findings and image pages"],
                    ["Before/After Pairs", str(total_ba), "Presented side-by-side"],
                ]
                t = Table(summary_data, colWidths=[2.5*inch, 1.2*inch, 2.6*inch])
                t.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                    ("SPAN", (0, 0), (-1, 0)),
                    ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                    ("GRID", (0, 1), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
                    ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f7fafc")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(t)
                story.append(PageBreak())

            # Image pages
            def make_image_block(img_file, label1, label2, comments, filename):
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp.write(img_file.getvalue())
                tmp.close()
                try:
                    pil = PILImage.open(tmp.name)
                    pil.thumbnail((480, 360))
                    pil.save(tmp.name, "JPEG", quality=90)
                    img = Image(tmp.name, width=3.4*inch, height=2.55*inch)
                except Exception:
                    img = Paragraph("[Image error]", styles["BodyJust"])

                meta = [
                    [Paragraph("<b>Label 1</b>", styles["BodyJust"]), Paragraph(label1 or "—", styles["BodyJust"])],
                    [Paragraph("<b>Label 2</b>", styles["BodyJust"]), Paragraph(label2 or "—", styles["BodyJust"])],
                    [Paragraph("<b>Comments</b>", styles["BodyJust"]), Paragraph(comments or "—", styles["BodyJust"])],
                ]
                meta_table = Table(meta, colWidths=[1.0*inch, 1.9*inch])
                meta_table.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e0")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f7fafc")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                ]))

                block = Table([[img, meta_table]], colWidths=[3.6*inch, 2.9*inch])
                block.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ]))
                return KeepTogether([
                    block,
                    Paragraph(filename, styles["SmallCenter"]),
                    Spacer(1, 0.12*inch)
                ])

            step = images_per_page
            for idx in range(0, len(image_metadata), step):
                chunk = image_metadata[idx:idx+step]
                for item in chunk:
                    story.append(make_image_block(
                        item["file"], item["label1"], item["label2"], item["comments"], item["name"]
                    ))
                if idx + step < len(image_metadata):
                    story.append(PageBreak())

            # Before/After
            for pair in ba_pairs:
                story.append(PageBreak())
                story.append(Paragraph(f"<b>{pair['label']}</b>", styles["SectionHead"]))
                story.append(Spacer(1, 0.1*inch))
                tmp_b = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp_b.write(pair["before"].getvalue())
                tmp_b.close()
                tmp_a = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp_a.write(pair["after"].getvalue())
                tmp_a.close()
                try:
                    img_b = Image(tmp_b.name, width=3.3*inch, height=2.5*inch)
                    img_a = Image(tmp_a.name, width=3.3*inch, height=2.5*inch)
                except Exception:
                    img_b = Paragraph("Before", styles["BodyJust"])
                    img_a = Paragraph("After", styles["BodyJust"])

                ba_table = Table([
                    [Paragraph("<b>BEFORE</b>", styles["SmallCenter"]), Paragraph("<b>AFTER</b>", styles["SmallCenter"])],
                    [img_b, img_a]
                ], colWidths=[3.4*inch, 3.4*inch])
                ba_table.setStyle(TableStyle([
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e0")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                    ("TOPPADDING", (0, 0), (-1, 0), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ]))
                story.append(ba_table)
                if pair["comments"]:
                    story.append(Spacer(1, 0.1*inch))
                    story.append(Paragraph(f"<b>Comments:</b> {pair['comments']}", styles["BodyJust"]))

            doc.build(story)
            buffer.seek(0)

            st.success("Customized report generated successfully.")
            st.download_button(
                label="Download PDF Report",
                data=buffer,
                file_name=f"{client_name}_{inspection_title.replace(' ', '_')}_{insp_date.strftime('%Y%m%d')}.pdf",
                mime="application/pdf"
            )

st.markdown("---")
st.caption("Applied Technical Services • Customizable Inspection Report Generator • For internal team use")
