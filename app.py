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


def clean_stem(name):
    stem = os.path.splitext(os.path.basename(name or ""))[0].lower()
    stem = re.sub(r"(before|after|pre|post|bef|aft|b4)", " ", stem)
    stem = re.sub(r"\b(19|20)\d{2}\b", " ", stem)
    stem = re.sub(r"\b\d{8}\b", " ", stem)
    stem = re.sub(r"\b\d{1,2}[-_]\d{1,2}[-_]\d{2,4}\b", " ", stem)
    stem = re.sub(r"[^a-z0-9]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()


def parse_point(text):
    """Pull the Pen or ICI number, and the view after it when present (Pen 1 0-90)."""
    if not text:
        return None
    m = re.search(
        r"(?i)\b(pen|ici)\s*[:#_\-]?\s*(\d+)\b(?:\s*(\d{1,3}\s*[-–]\s*\d{1,3}))?",
        text,
    )
    if not m:
        return None
    kind = "ICI" if m.group(1).lower() == "ici" else "Pen"
    num = int(m.group(2))
    view = re.sub(r"\s+", "", m.group(3) or "")
    label = f"{kind} {num}" + (f" {view}" if view else "")
    return {"kind": kind.lower(), "num": num, "view": view.lower(), "label": label}


def read_point(name, data):
    found = parse_point(name)
    if found:
        return found["label"]
    try:
        import pytesseract
        im = PILImage.open(io.BytesIO(data)).convert("RGB")
        w, h = im.size
        crops = [
            im.crop((0, 0, w, max(20, int(h * 0.14)))),
            im.crop((0, 0, int(w * 0.5), max(20, int(h * 0.2)))),
        ]
        bits = []
        for crop in crops:
            crop = crop.resize((crop.width * 3, crop.height * 3))
            bits.append(pytesseract.image_to_string(crop, config="--psm 6") or "")
        found = parse_point(" ".join(bits))
        return found["label"] if found else ""
    except Exception:
        return ""


def point_sort_key(img):
    info = parse_point(img.get("point") or "") or parse_point(img.get("name") or "")
    if not info:
        return ("zzz", 9999, "", natural_key(img.get("name") or ""))
    return (info["kind"], info["num"], info["view"], natural_key(img.get("name") or ""))


def add_images(items, role="standard"):
    """items: list of (name, bytes). Same filename may exist in before and after."""
    library = st.session_state.library
    known = {(img["role"], img["name"], len(img["bytes"])) for img in library}
    added = 0
    for name, data in items:
        base = os.path.basename(name.replace("\\", "/"))
        ext = os.path.splitext(base)[1].lower()
        if ext not in IMAGE_EXTS or not data:
            continue
        use_role = role if role != "auto" else detect_role(base)
        if (use_role, base, len(data)) in known:
            continue
        library.append({
            "id": uuid.uuid4().hex[:10],
            "name": base,
            "folder": os.path.dirname(name.replace("\\", "/")),
            "bytes": data,
            "label1": os.path.splitext(base)[0],
            "label2": "",
            "comments": "",
            "role": use_role,
            "point": read_point(base, data),
        })
        known.add((use_role, base, len(data)))
        added += 1
    if added and not st.session_state.manual_order:
        library.sort(key=lambda img: (img["role"], natural_key(img["folder"] + "/" + img["name"])))
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


def match_sets(library, mode="point"):
    """Pair older photos to new photos that share the same Pen or ICI number."""
    befores = [img for img in library if img["role"] == "before"]
    afters = [img for img in library if img["role"] == "after"]
    befores.sort(key=point_sort_key)
    afters.sort(key=point_sort_key)
    buckets = {}
    for a in afters:
        info = parse_point(a.get("point") or "") or parse_point(a.get("name") or "")
        key = (info["kind"], info["num"]) if info else None
        buckets.setdefault(key, []).append(a)
    used = set()
    pairs = []
    for b in befores:
        info = parse_point(b.get("point") or "") or parse_point(b.get("name") or "")
        if not info:
            continue
        choices = [a for a in buckets.get((info["kind"], info["num"]), []) if a["id"] not in used]
        if not choices:
            continue
        if info["view"]:
            exact = [a for a in choices if (parse_point(a.get("point") or "") or {}).get("view") == info["view"]]
            if exact:
                choices = exact
        chosen = choices[0]
        used.add(chosen["id"])
        pairs.append((b, chosen))
    paired_b = {b["id"] for b, _ in pairs}
    paired_a = {a["id"] for _, a in pairs}
    left_b = [b for b in befores if b["id"] not in paired_b]
    left_a = [a for a in afters if a["id"] not in paired_a]
    pairs.sort(key=lambda pair: point_sort_key(pair[0]))
    return pairs, left_b, left_a


def files_from_upload(files):
    incoming = []
    for f in files or []:
        if f.name.lower().endswith(".zip"):
            incoming.extend(images_from_zip(f.getvalue()))
        else:
            incoming.append((f.name, f.getvalue()))
    return incoming


if "library" not in st.session_state:
    st.session_state.library = []
if "manual_order" not in st.session_state:
    st.session_state.manual_order = False
if "upload_nonce" not in st.session_state:
    st.session_state.upload_nonce = 0
if "nonce_before" not in st.session_state:
    st.session_state.nonce_before = 0
if "nonce_after" not in st.session_state:
    st.session_state.nonce_after = 0
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
    images_per_page = st.selectbox("Single photos per page", [2, 4, 6], index=1)
    custom_cover_note = st.text_input("Extra cover note (optional)", "")

client_logo_path = None
if include_client_logo:
    client_logo_file = st.file_uploader("Client logo", type=["png", "jpg", "jpeg"], key="client_logo")
    if client_logo_file:
        client_logo_path = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name
        with open(client_logo_path, "wb") as f:
            f.write(client_logo_file.getvalue())

st.markdown("---")
st.subheader("Images for this report")
st.caption("Photos that are not a before/after. Select many, or drop a .zip. They print in filename order.")

report_files = st.file_uploader(
    "Report photos or a .zip",
    type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "zip"],
    accept_multiple_files=True,
    key=f"uploads_{st.session_state.upload_nonce}",
)
added_report = add_images(files_from_upload(report_files), role="standard") if report_files else 0

st.markdown("---")
st.subheader("Before and after — two inspections")
st.caption(
    "Older inspection on the left, the one you just did on the right. "
    "They stay separate. A before photo is matched to an after photo only when both say the same Pen or ICI number "
    "(Pen 1 with Pen 1, ICI 4 with ICI 4). Photos with no match print by themselves at the end."
)

col_b, col_a = st.columns(2)
with col_b:
    st.markdown("**Older inspection (before)**")
    before_files = st.file_uploader(
        "Before photos or a .zip",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "zip"],
        accept_multiple_files=True,
        key=f"before_up_{st.session_state.nonce_before}",
    )
with col_a:
    st.markdown("**This inspection (after)**")
    after_files = st.file_uploader(
        "After photos or a .zip",
        type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "zip"],
        accept_multiple_files=True,
        key=f"after_up_{st.session_state.nonce_after}",
    )

if before_files:
    add_images(files_from_upload(before_files), role="before")
if after_files:
    add_images(files_from_upload(after_files), role="after")
if added_report:
    st.session_state.img_page = 0

library = st.session_state.library
n_before = sum(1 for img in library if img["role"] == "before")
n_after = sum(1 for img in library if img["role"] == "after")
match_mode = "point"

c1, c2, c3 = st.columns(3)
with c1:
    if st.button("Clear older inspection"):
        st.session_state.library = [img for img in library if img["role"] != "before"]
        st.session_state.nonce_before += 1
        st.rerun()
with c2:
    if st.button("Clear this inspection"):
        st.session_state.library = [img for img in library if img["role"] != "after"]
        st.session_state.nonce_after += 1
        st.rerun()
with c3:
    if st.button("Clear all images"):
        st.session_state.library = []
        st.session_state.manual_order = False
        st.session_state.upload_nonce += 1
        st.session_state.nonce_before += 1
        st.session_state.nonce_after += 1
        st.session_state.img_page = 0
        st.rerun()

role_labels = {
    "standard": "In the report",
    "before": "Older inspection",
    "after": "This inspection",
    "skip": "Leave out",
}

pairs_preview, left_before, left_after = match_sets(library, match_mode)

def edit_set(images, empty_note):
    if not images:
        st.caption(empty_note)
        return
    for img in images:
        tag = img.get("point") or "No Pen / ICI yet"
        st.image(img["bytes"], width=240, caption=img["name"])
        img["point"] = st.text_input(
            "Pen or ICI",
            value=img.get("point") or "",
            key=f"pt_{img['id']}",
            placeholder="Pen 1 or ICI 3",
            label_visibility="collapsed",
        )
        st.caption(tag if img.get("point") else "Type the Pen or ICI number if it was not picked up.")

if library:
    n_std = sum(1 for img in library if img["role"] == "standard")
    missing = sum(1 for img in library if img["role"] in ("before", "after") and not (img.get("point") or parse_point(img["name"])))
    st.info(
        f"Older: {n_before} · This inspection: {n_after} · Other photos: {n_std} · "
        f"Matched pairs: {len(pairs_preview)}"
    )
    if missing:
        st.warning(
            f"{missing} photo(s) have no Pen or ICI number. Type it under the photo (example: Pen 1). "
            "Those print alone at the end until they match."
        )

    left_col, right_col = st.columns(2)
    with left_col:
        st.markdown(f"**Older inspection ({n_before})**")
        edit_set(sorted((img for img in library if img["role"] == "before"), key=point_sort_key), "No older photos yet.")
    with right_col:
        st.markdown(f"**This inspection ({n_after})**")
        edit_set(sorted((img for img in library if img["role"] == "after"), key=point_sort_key), "No new photos yet.")

    if pairs_preview:
        st.markdown("**Matched by Pen / ICI**")
        for b, a in pairs_preview:
            label = b.get("point") or a.get("point") or "Pair"
            st.write(f"{label}:  {b['name']}  ↔  {a['name']}")
    singles = [img for img in library if img["role"] == "standard"] + left_before + left_after
    if singles:
        st.markdown("**Single photos — these print at the end**")
        st.write(", ".join((img.get("point") or img["name"]) for img in singles[:30]))
else:
    st.info("No images yet. Add report photos, or the two inspections above.")

# Generate PDF
if st.button("Generate Customized Report PDF", type="primary"):
    if not client_name or not inspection_title:
        st.error("Please fill in at least Client Name and Inspection Title.")
    else:
        library = st.session_state.library
        paired, left_b, left_a = match_sets(library, "point")
        image_metadata = [img for img in library if img["role"] == "standard"] + left_b + left_a
        image_metadata.sort(key=point_sort_key)
        ba_pairs = []
        for b, a in paired:
            ba_pairs.append({
                "before_bytes": b["bytes"],
                "after_bytes": a["bytes"],
                "before_name": b["name"],
                "after_name": a["name"],
                "label": b.get("point") or a.get("point") or "Before / After",
                "comments": " · ".join(x for x in (b.get("comments"), a.get("comments")) if x),
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

            pairs_on_page = 1 if images_per_page <= 2 else 2
            for i, pair in enumerate(ba_pairs):
                if i > 0 and i % pairs_on_page == 0:
                    story.append(PageBreak())
                story.append(Paragraph(f"<b>{pair['label']}</b>", styles["SectionHead"]))
                story.append(Paragraph(
                    f"{pair['before_name']}  ·  {pair['after_name']}",
                    styles["SmallCenter"],
                ))
                story.append(Spacer(1, 0.06*inch))
                pair_h = 2.5*inch if pairs_on_page == 1 else 2.05*inch
                try:
                    img_b = Image(jpeg_path(pair["before_bytes"]), width=3.3*inch, height=pair_h)
                    img_a = Image(jpeg_path(pair["after_bytes"]), width=3.3*inch, height=pair_h)
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
                    ("TOPPADDING", (0, 0), (-1, 0), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                ]))
                story.append(ba_table)
                if pair["comments"]:
                    story.append(Spacer(1, 0.06*inch))
                    story.append(Paragraph(f"<b>Comments:</b> {pair['comments']}", styles["BodyJust"]))
                story.append(Spacer(1, 0.12*inch))

            if image_metadata:
                if ba_pairs:
                    story.append(PageBreak())
                story.append(Paragraph("<b>Single photos</b>", styles["SectionHead"]))
                per = images_per_page if images_per_page in (2, 4, 6) else 4
                img_h = {2: 3.0*inch, 4: 2.25*inch, 6: 1.65*inch}[per]
                for idx in range(0, len(image_metadata), per):
                    if idx:
                        story.append(PageBreak())
                        story.append(Paragraph("<b>Single photos</b>", styles["SectionHead"]))
                    chunk = image_metadata[idx:idx + per]
                    rows = []
                    for r in range(0, len(chunk), 2):
                        pics, caps = [], []
                        for item in chunk[r:r + 2]:
                            try:
                                pics.append(Image(jpeg_path(item["bytes"]), width=3.3*inch, height=img_h))
                            except Exception:
                                pics.append(Paragraph("[Image error]", styles["BodyJust"]))
                            tag = item.get("point") or item.get("label1") or ""
                            caps.append(Paragraph(f"{tag}<br/>{item['name']}", styles["SmallCenter"]))
                        while len(pics) < 2:
                            pics.append("")
                            caps.append("")
                        rows.append(pics)
                        rows.append(caps)
                    grid = Table(rows, colWidths=[3.6*inch, 3.6*inch])
                    grid.setStyle(TableStyle([
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 3),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                        ("TOPPADDING", (0, 0), (-1, -1), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]))
                    story.append(grid)

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
