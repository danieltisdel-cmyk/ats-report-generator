import streamlit as st
import io
import json
import re
import uuid
import zipfile
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

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def natural_key(name):
    parts = re.split(r"(\d+)", (name or "").lower())
    return [int(p) if p.isdigit() else p for p in parts]


def detect_role(name):
    n = (name or "").lower()
    if re.search(r"(before|pre[_\-\s]?shot|\bpre\b|_bef\b|_b4\b)", n):
        return "before"
    if re.search(r"(after|post[_\-\s]?shot|\bpost\b|_aft\b)", n):
        return "after"
    return "standard"


def pair_key(name):
    stem = os.path.splitext(os.path.basename(name or ""))[0]
    stem = re.sub(r"(?i)[\s_\-]*(before|after|pre|post|bef|aft|b4)\b", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" _-").lower()
    return stem


def add_images(items):
    """items: list of (name, bytes). Skips duplicates and non-images."""
    library = st.session_state.library
    known = {(img["name"], len(img["bytes"])) for img in library}
    added = 0
    for name, data in items:
        base = os.path.basename(name.replace("\\", "/"))
        ext = os.path.splitext(base)[1].lower()
        if ext not in IMAGE_EXTS or not data:
            continue
        if (base, len(data)) in known:
            continue
        library.append({
            "id": uuid.uuid4().hex[:10],
            "name": base,
            "folder": os.path.dirname(name.replace("\\", "/")),
            "bytes": data,
            "label1": os.path.splitext(base)[0],
            "label2": "",
            "comments": "",
            "role": detect_role(base),
        })
        known.add((base, len(data)))
        added += 1
    if added and not st.session_state.manual_order:
        library.sort(key=lambda img: natural_key(img["folder"] + "/" + img["name"]))
    return added


def images_from_zip(data):
    found = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return found
    for info in zf.infolist():
        if info.is_dir():
            continue
        found.append((info.filename, zf.read(info)))
    return found


def build_pairs(library):
    befores = [img for img in library if img["role"] == "before"]
    afters = [img for img in library if img["role"] == "after"]
    used_after = set()
    pairs = []
    for b in befores:
        key = pair_key(b["name"])
        match = next((a for a in afters if a["id"] not in used_after and pair_key(a["name"]) == key), None)
        if match is None:
            bnums = re.findall(r"\d+", b["name"])
            if bnums:
                match = next(
                    (a for a in afters if a["id"] not in used_after and bnums[-1] in re.findall(r"\d+", a["name"])),
                    None,
                )
        if match is not None:
            used_after.add(match["id"])
            pairs.append((b, match))
    return pairs


if "library" not in st.session_state:
    st.session_state.library = []
if "manual_order" not in st.session_state:
    st.session_state.manual_order = False
if "upload_nonce" not in st.session_state:
    st.session_state.upload_nonce = 0
if "img_page" not in st.session_state:
    st.session_state.img_page = 0

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
st.caption("Drop a whole folder as a .zip, or select many photos at once. They sort by the numbers in the filename (2 before 10) and stay in that order.")

up1, up2 = st.columns(2)
with up1:
    uploaded_images = st.file_uploader(
        "Images — select as many as you want",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff"],
        accept_multiple_files=True,
        key=f"std_images_{st.session_state.upload_nonce}",
    )
with up2:
    uploaded_zips = st.file_uploader(
        "Or a .zip of the whole folder",
        type=["zip"],
        accept_multiple_files=True,
        key=f"zip_images_{st.session_state.upload_nonce}",
    )

incoming = []
if uploaded_images:
    incoming.extend((f.name, f.getvalue()) for f in uploaded_images)
if uploaded_zips:
    for z in uploaded_zips:
        incoming.extend(images_from_zip(z.getvalue()))
if incoming:
    added = add_images(incoming)
    if added:
        st.session_state.img_page = 0

library = st.session_state.library
b1, b2, b3, b4 = st.columns(4)
with b1:
    if st.button("Sort by filename"):
        library.sort(key=lambda img: natural_key(img["folder"] + "/" + img["name"]))
        st.session_state.manual_order = False
        st.rerun()
with b2:
    if st.button("Tag before/after from names"):
        for img in library:
            img["role"] = detect_role(img["name"])
        st.rerun()
with b3:
    if st.button("Mark all standard"):
        for img in library:
            img["role"] = "standard"
        st.rerun()
with b4:
    if st.button("Clear all images"):
        st.session_state.library = []
        st.session_state.manual_order = False
        st.session_state.upload_nonce += 1
        st.session_state.img_page = 0
        st.rerun()

role_labels = {
    "standard": "In report",
    "before": "Before",
    "after": "After",
    "skip": "Leave out",
}

if library:
    n_std = sum(1 for img in library if img["role"] == "standard")
    pairs_preview = build_pairs(library)
    st.info(f"{len(library)} images loaded · {n_std} in order · {len(pairs_preview)} before/after pair(s).")

    query = st.text_input("Find an image", placeholder="Type part of a filename")
    shown = [img for img in library if not query or query.lower() in img["name"].lower() or query.lower() in (img.get("folder") or "").lower()]

    page_size = 12
    pages = max(1, (len(shown) + page_size - 1) // page_size)
    st.session_state.img_page = min(st.session_state.img_page, pages - 1)
    nav1, nav2, nav3 = st.columns([1, 2, 1])
    with nav1:
        if st.button("Previous page", disabled=st.session_state.img_page <= 0):
            st.session_state.img_page -= 1
            st.rerun()
    with nav2:
        st.markdown(f"Page {st.session_state.img_page + 1} of {pages}")
    with nav3:
        if st.button("Next page", disabled=st.session_state.img_page >= pages - 1):
            st.session_state.img_page += 1
            st.rerun()

    start = st.session_state.img_page * page_size
    for offset, img in enumerate(shown[start:start + page_size]):
        idx = library.index(img)
        with st.expander(f"{idx + 1}. {img['name']}  —  {role_labels.get(img['role'], img['role'])}", expanded=False):
            left, right = st.columns([1, 2])
            with left:
                st.image(img["bytes"], width=220)
            with right:
                img["role"] = st.selectbox(
                    "Use as",
                    list(role_labels.keys()),
                    index=list(role_labels.keys()).index(img["role"]) if img["role"] in role_labels else 0,
                    format_func=lambda r: role_labels[r],
                    key=f"role_{img['id']}",
                )
                img["label1"] = st.text_input("Label 1", value=img["label1"], key=f"l1_{img['id']}")
                img["label2"] = st.text_input("Label 2", value=img["label2"], key=f"l2_{img['id']}")
                img["comments"] = st.text_input("Comments", value=img["comments"], key=f"c_{img['id']}")
                m1, m2, m3 = st.columns(3)
                with m1:
                    if st.button("Move up", key=f"up_{img['id']}", disabled=idx == 0):
                        library[idx - 1], library[idx] = library[idx], library[idx - 1]
                        st.session_state.manual_order = True
                        st.rerun()
                with m2:
                    if st.button("Move down", key=f"dn_{img['id']}", disabled=idx >= len(library) - 1):
                        library[idx + 1], library[idx] = library[idx], library[idx + 1]
                        st.session_state.manual_order = True
                        st.rerun()
                with m3:
                    if st.button("Remove", key=f"rm_{img['id']}"):
                        library.pop(idx)
                        st.rerun()

    if pairs_preview:
        st.markdown("**Before/after pairs that will sit side by side**")
        for b, a in pairs_preview:
            st.write(f"{b['name']}  →  {a['name']}")
else:
    st.info("No images yet. Select a batch of photos, or zip the Mentor folder and upload the .zip.")

# Generate PDF
if st.button("Generate Customized Report PDF", type="primary"):
    if not client_name or not inspection_title:
        st.error("Please fill in at least Client Name and Inspection Title.")
    else:
        library = st.session_state.library
        image_metadata = [img for img in library if img["role"] == "standard"]
        ba_pairs = []
        for b, a in build_pairs(library):
            ba_pairs.append({
                "before_bytes": b["bytes"],
                "after_bytes": a["bytes"],
                "before_name": b["name"],
                "after_name": a["name"],
                "label": b["label1"] or pair_key(b["name"]) or "Before / After",
                "comments": " · ".join(x for x in (b["comments"], a["comments"]) if x),
            })
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

            # Boxed sections — skip blanks, one column so the text is not dropped
            box_title = ParagraphStyle(
                "BoxTitle",
                parent=styles["Normal"],
                fontName="Helvetica-Bold",
                fontSize=11,
                textColor=accent,
                leading=14,
                spaceBefore=0,
                spaceAfter=0,
            )

            def boxed_section(title, content):
                text = (content or "").strip()
                if not text:
                    return None
                data = [
                    [Paragraph(title, box_title)],
                    [Paragraph(text.replace("\n", "<br/>"), styles["BodyJust"])],
                ]
                t = Table(data, colWidths=[7.2*inch])
                t.setStyle(TableStyle([
                    ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#cbd5e0")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]))
                return t

            for title, content in (("Scope", scope), ("Access", access), ("Findings", findings)):
                block = boxed_section(title, content)
                if block is not None:
                    story.append(block)
                    story.append(Spacer(1, 0.12*inch))

            if show_signoff and (signoff_name or "").strip():
                signoff_data = [[Paragraph("Signoff", box_title)],
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

            has_images = bool(image_metadata or ba_pairs)
            if show_summary or has_images:
                story.append(PageBreak())

            # Summary
            if show_summary:
                total_std = len(image_metadata)
                total_ba = len(ba_pairs)
                summary_data = [
                    [Paragraph("<b>Inspection Summary</b>", styles["BodyJust"]), "", ""],
                    [Paragraph("<b>Category</b>", styles["BodyJust"]), Paragraph("<b>Count</b>", styles["BodyJust"]), Paragraph("<b>Notes</b>", styles["BodyJust"])],
                    ["Standard Images", str(total_std), "In filename order"],
                    ["Before/After Pairs", str(total_ba), "Side-by-side"],
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
                if has_images:
                    story.append(PageBreak())

            def jpeg_path(data):
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                pil = PILImage.open(io.BytesIO(data))
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                pil.thumbnail((480, 360))
                pil.save(tmp.name, "JPEG", quality=90)
                tmp.close()
                return tmp.name

            def make_image_block(data, label1, label2, comments, filename):
                try:
                    img = Image(jpeg_path(data), width=3.4*inch, height=2.55*inch)
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
            wrote_std = False
            for idx in range(0, len(image_metadata), step):
                wrote_std = True
                chunk = image_metadata[idx:idx+step]
                for item in chunk:
                    story.append(make_image_block(
                        item["bytes"], item["label1"], item["label2"], item["comments"], item["name"]
                    ))
                if idx + step < len(image_metadata):
                    story.append(PageBreak())

            for i, pair in enumerate(ba_pairs):
                if wrote_std or i > 0:
                    story.append(PageBreak())
                story.append(Paragraph(f"<b>{pair['label']}</b>", styles["SectionHead"]))
                story.append(Paragraph(
                    f"{pair['before_name']}  ·  {pair['after_name']}",
                    styles["SmallCenter"],
                ))
                story.append(Spacer(1, 0.08*inch))
                try:
                    img_b = Image(jpeg_path(pair["before_bytes"]), width=3.3*inch, height=2.5*inch)
                    img_a = Image(jpeg_path(pair["after_bytes"]), width=3.3*inch, height=2.5*inch)
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
