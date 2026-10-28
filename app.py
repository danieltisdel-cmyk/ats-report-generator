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
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


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
    """Only the number after Pen/ICI and the degree range. Pen6_0-90 and ICI_6_0-90 are the same."""
    if not text:
        return None
    m = re.search(
        r"(?i)(?<![A-Za-z])(?:pen|ici)[_\-\s]*(\d+)(?:[_\-\s]+(\d{1,3})\s*[-–]\s*(\d{1,3}))?",
        text,
    )
    if not m:
        m = re.search(r"(?<!\d)(\d+)\s+(\d{1,3})\s*[-–]\s*(\d{1,3})", text)
        if not m:
            return None
    num = int(m.group(1))
    if m.group(2) and m.group(3):
        view = f"{int(m.group(2))}-{int(m.group(3))}"
    else:
        view = ""
    label = f"{num} {view}".strip()
    return {"num": num, "view": view, "label": label}


def is_flag(name):
    return bool(re.search(r"(?i)flag", name or ""))


def view_start(view):
    m = re.match(r"(\d+)", view or "")
    return int(m.group(1)) if m else 999


def read_point(name, data=None):
    """Filename only. Scanning the picture itself is a separate button so uploads stay fast."""
    found = parse_point(name)
    return found["label"] if found else ""


def shrink_photo(data, max_px=1200, quality=80):
    """Smaller JPEG so a whole inspection folder does not freeze the page."""
    im = PILImage.open(io.BytesIO(data))
    im = im.convert("RGB")
    im.thumbnail((max_px, max_px))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality)
    return out.getvalue()


def thumb_photo(data):
    im = PILImage.open(io.BytesIO(data))
    im = im.convert("RGB")
    im.thumbnail((280, 210))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=65)
    return out.getvalue()


def ocr_banner(data):
    """Read the Pen / ICI line Mentor prints along the top of the still."""
    try:
        import pytesseract
        im = PILImage.open(io.BytesIO(data)).convert("L")
        w, h = im.size
        strip = im.crop((0, 0, w, max(18, int(h * 0.12))))
        strip = strip.resize((strip.width * 2, max(48, strip.height * 2)))
        text = pytesseract.image_to_string(strip, config="--psm 6") or ""
        found = parse_point(text)
        return found["label"] if found else ""
    except Exception:
        return ""


def point_sort_key(img):
    info = parse_point(img.get("name") or "") or parse_point(img.get("point") or "")
    name_key = natural_key(img.get("name") or "")
    flagged = 1 if is_flag(img.get("name") or "") else 0
    if not info or not info.get("view"):
        return (1, 0, 999, flagged, name_key)
    return (0, int(info["num"]), view_start(info["view"]), flagged, name_key)


def point_info(img):
    """Number and degrees only. Filename first, because it matches the top-left stamp."""
    info = parse_point(img.get("name") or "")
    if not info or not info.get("view"):
        typed = parse_point(img.get("point") or "")
        if typed and typed.get("view"):
            info = typed
    if info and info.get("view"):
        img["point"] = info["label"]
    return info


def add_images(items, role="standard"):
    """items: list of (name, bytes). Same filename may exist in before and after."""
    library = st.session_state.library
    known = {(img["role"], img["name"]) for img in library}
    added = 0
    for name, data in items:
        base = os.path.basename(name.replace("\\", "/"))
        ext = os.path.splitext(base)[1].lower()
        if ext not in IMAGE_EXTS or not data:
            continue
        use_role = role if role != "auto" else detect_role(base)
        if (use_role, base) in known:
            continue
        try:
            stored = shrink_photo(data)
            thumb = thumb_photo(stored)
        except Exception:
            stored = data
            thumb = data
        library.append({
            "id": uuid.uuid4().hex[:10],
            "name": base,
            "folder": os.path.dirname(name.replace("\\", "/")),
            "bytes": stored,
            "thumb": thumb,
            "label1": os.path.splitext(base)[0],
            "label2": "",
            "comments": "",
            "role": use_role,
            "point": read_point(base),
        })
        known.add((use_role, base))
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
    """Pen and ICI match on the number and the degree range. Pen6_0-90 pairs with ICI_6_0-90."""
    for img in library:
        point_info(img)
    befores = [img for img in library if img["role"] == "before"]
    afters = [img for img in library if img["role"] == "after"]
    befores.sort(key=point_sort_key)
    afters.sort(key=point_sort_key)
    buckets = {}
    for a in afters:
        info = point_info(a)
        if not info or not info["view"]:
            continue
        key = (info["num"], info["view"])
        buckets.setdefault(key, []).append(a)
    used = set()
    pairs = []
    for b in befores:
        info = point_info(b)
        if not info or not info["view"]:
            continue
        key = (info["num"], info["view"])
        choices = [a for a in buckets.get(key, []) if a["id"] not in used]
        if not choices:
            continue
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
    """Read each dropped file once. Later clicks reuse what is already stored."""
    incoming = []
    seen = st.session_state.seen_uploads
    for f in files or []:
        fid = getattr(f, "file_id", None)
        if not fid:
            fid = f"{f.name}:{getattr(f, 'size', 0)}"
        if fid in seen:
            continue
        raw = f.getvalue()
        if f.name.lower().endswith(".zip"):
            incoming.extend(images_from_zip(raw))
        else:
            incoming.append((f.name, raw))
        seen.add(fid)
    return incoming


if "library" not in st.session_state:
    st.session_state.library = []
if "seen_uploads" not in st.session_state:
    st.session_state.seen_uploads = set()
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
    photo_layout = st.selectbox(
        "Regular photo layout",
        [
            "ATS — photos stacked, info block beside each",
            "Grid — 4 per page",
            "Grid — 6 per page",
        ],
        index=0,
    )
    custom_cover_note = st.text_input("Extra cover note (optional)", "")

client_logo_path = None
if include_client_logo:
    client_logo_file = st.file_uploader("Client logo", type=["png", "jpg", "jpeg"], key="client_logo")
    if client_logo_file:
        client_logo_path = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name
        with open(client_logo_path, "wb") as f:
            f.write(client_logo_file.getvalue())

st.markdown("---")
st.subheader("Regular inspection")
st.caption(
    "One set of photos, not a before/after. They print like the ATS reports: "
    "two photos down the page, filename under each photo, and Label 1 / Label 2 / Comments beside it."
)

report_files = st.file_uploader(
    "Inspection photos or a .zip",
    type=["jpg", "jpeg", "png", "webp", "bmp", "tif", "tiff", "zip"],
    accept_multiple_files=True,
    key=f"uploads_{st.session_state.upload_nonce}",
)
added_report = add_images(files_from_upload(report_files), role="standard") if report_files else 0

library = st.session_state.library
regular = [img for img in library if img["role"] == "standard"]
if regular:
    st.info(f"{len(regular)} regular inspection photo(s), in filename order.")
    b1, b2 = st.columns(2)
    with b1:
        bulk_l1 = st.text_input("Label 1 for every photo", key="bulk_l1", placeholder="Attemperator")
    with b2:
        bulk_l2 = st.text_input("Label 2 for every photo", key="bulk_l2", placeholder="6A MS 546")
    if st.button("Apply these labels to every regular photo"):
        for img in regular:
            if bulk_l1:
                img["label1"] = bulk_l1
            if bulk_l2:
                img["label2"] = bulk_l2
        st.rerun()
    if "page_regular" not in st.session_state:
        st.session_state.page_regular = 0
    page_size = 6
    pages = max(1, (len(regular) + page_size - 1) // page_size)
    page = min(st.session_state.page_regular, pages - 1)
    st.session_state.page_regular = page
    for img in regular[page * page_size:(page + 1) * page_size]:
        with st.expander(img["name"], expanded=False):
            c1, c2 = st.columns([1, 2])
            with c1:
                st.image(img.get("thumb") or img["bytes"], width=200)
            with c2:
                img["label1"] = st.text_input("Label 1", value=img.get("label1") or "", key=f"rl1_{img['id']}")
                img["label2"] = st.text_input("Label 2", value=img.get("label2") or "", key=f"rl2_{img['id']}")
                img["comments"] = st.text_input("Comments", value=img.get("comments") or "", key=f"rc_{img['id']}")
    if pages > 1:
        n1, n2, n3 = st.columns([1, 2, 1])
        with n1:
            if st.button("Previous photos", disabled=page <= 0):
                st.session_state.page_regular -= 1
                st.rerun()
        with n2:
            st.caption(f"Page {page + 1} of {pages}")
        with n3:
            if st.button("Next photos", disabled=page >= pages - 1):
                st.session_state.page_regular += 1
                st.rerun()

st.markdown("---")
st.subheader("Before and after — two inspections")
st.caption(
    "Older inspection on the left, the one you just did on the right. "
    "Matching uses only the number after Pen or ICI, and the degree range after that. "
    "Pen6_0-90 matches ICI_6_0-90. The word Pen or ICI does not matter. "
    "001 and FLAG on the end of the name do not matter. 0-90 does not match 90-180."
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
        st.session_state.seen_uploads = set()
        st.session_state.nonce_before += 1
        st.rerun()
with c2:
    if st.button("Clear this inspection"):
        st.session_state.library = [img for img in library if img["role"] != "after"]
        st.session_state.seen_uploads = set()
        st.session_state.nonce_after += 1
        st.rerun()
with c3:
    if st.button("Clear all images"):
        st.session_state.library = []
        st.session_state.seen_uploads = set()
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

def edit_set(images, empty_note, page_key):
    if not images:
        st.caption(empty_note)
        return
    page_size = 6
    pages = max(1, (len(images) + page_size - 1) // page_size)
    page = min(st.session_state.get(page_key, 0), pages - 1)
    st.session_state[page_key] = page
    start = page * page_size
    for img in images[start:start + page_size]:
        st.image(img.get("thumb") or img["bytes"], width=220, caption=img["name"])
        shown = (point_info(img) or {}).get("label") or "No number and angle in the name"
        st.caption(shown)
        img["point"] = st.text_input(
            "Number and degrees",
            value=img.get("point") or "",
            key=f"pt_{img['id']}",
            placeholder="6 0-90",
            label_visibility="collapsed",
        )
    if pages > 1:
        a, b = st.columns(2)
        with a:
            if st.button("Previous", key=f"prev_{page_key}", disabled=page <= 0):
                st.session_state[page_key] = page - 1
                st.rerun()
        with b:
            if st.button("Next", key=f"next_{page_key}", disabled=page >= pages - 1):
                st.session_state[page_key] = page + 1
                st.rerun()
        st.caption(f"Page {page + 1} of {pages}")

if library:
    n_std = sum(1 for img in library if img["role"] == "standard")
    missing = sum(
        1 for img in library
        if img["role"] in ("before", "after") and not ((point_info(img) or {}).get("view"))
    )
    st.info(
        f"Older: {n_before} · This inspection: {n_after} · Other photos: {n_std} · "
        f"Matched pairs: {len(pairs_preview)}"
    )
    if missing:
        st.warning(
            f"{missing} photo(s) have no number and degree range in the filename. "
            "Those print alone at the end. Type it under the photo as 6 0-90 if the name is wrong."
        )
        if st.button("Read Pen/ICI from the next 20 photos"):
            todo = [img for img in library if img["role"] in ("before", "after") and not img.get("point")][:20]
            found = 0
            for img in todo:
                label = ocr_banner(img["bytes"])
                if label:
                    img["point"] = label
                    found += 1
            st.session_state["ocr_note"] = f"Read {found} of {len(todo)}. Click again if some are still blank."
            st.rerun()
        if st.session_state.get("ocr_note"):
            st.caption(st.session_state["ocr_note"])

    left_col, right_col = st.columns(2)
    with left_col:
        st.markdown(f"**Older inspection ({n_before})**")
        edit_set(sorted((img for img in library if img["role"] == "before"), key=point_sort_key), "No older photos yet.", "page_before")
    with right_col:
        st.markdown(f"**This inspection ({n_after})**")
        edit_set(sorted((img for img in library if img["role"] == "after"), key=point_sort_key), "No new photos yet.", "page_after")

    if pairs_preview:
        st.markdown("**Matched by number and degrees**")
        for b, a in pairs_preview:
            info = parse_point(b.get("point") or "") or parse_point(a.get("point") or "") or {}
            view = info.get("view") or ""
            num = info.get("num")
            tag = f"{num} {view}".strip() if num is not None else (b.get("point") or "Pair")
            st.write(f"{tag}:  {b.get('point') or b['name']}  ↔  {a.get('point') or a['name']}")
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
            info = point_info(b) or point_info(a) or {}
            label = info.get("label") or "Before / After"
            ba_pairs.append({
                "before_bytes": b["bytes"],
                "after_bytes": a["bytes"],
                "before_name": b["name"],
                "after_name": a["name"],
                "label": label,
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
                pil.thumbnail((1000, 750))
                pil.save(tmp.name, "JPEG", quality=90)
                tmp.close()
                return tmp.name

            def make_image_block(data, label1, label2, comments, filename):
                try:
                    img = Image(jpeg_path(data), width=4.05*inch, height=3.0*inch)
                except Exception:
                    img = Paragraph("[Image error]", styles["BodyJust"])

                meta = [
                    [Paragraph("<b>Label1</b>", styles["BodyJust"]), Paragraph(label1 or "", styles["BodyJust"])],
                    [Paragraph("<b>Label2</b>", styles["BodyJust"]), Paragraph(label2 or "", styles["BodyJust"])],
                    [Paragraph("<b>Comments</b>", styles["BodyJust"]), Paragraph(comments or "", styles["BodyJust"])],
                ]
                meta_table = Table(meta, colWidths=[0.9*inch, 1.9*inch])
                meta_table.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                    ("BACKGROUND", (0, 0), (0, -1), colors.white),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                ]))

                block = Table(
                    [[img, meta_table], [Paragraph(filename, styles["SmallCenter"]), ""]],
                    colWidths=[4.2*inch, 2.9*inch],
                )
                block.setStyle(TableStyle([
                    ("VALIGN", (0, 0), (1, 0), "TOP"),
                    ("ALIGN", (0, 1), (0, 1), "CENTER"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ("SPAN", (0, 1), (0, 1)),
                ]))
                return KeepTogether([block, Spacer(1, 0.18*inch)])

            ats_layout = photo_layout.startswith("ATS")
            images_per_page = 2 if ats_layout else (4 if "4" in photo_layout else 6)
            pairs_on_page = 1 if ats_layout else 2
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
                if ats_layout:
                    for idx, item in enumerate(image_metadata):
                        if idx and idx % 2 == 0:
                            story.append(PageBreak())
                        story.append(make_image_block(
                            item["bytes"], item.get("label1") or "", item.get("label2") or "",
                            item.get("comments") or "", item["name"],
                        ))
                else:
                    story.append(Paragraph("<b>Single photos</b>", styles["SectionHead"]))
                    per = images_per_page
                    img_h = {4: 2.25*inch, 6: 1.65*inch}.get(per, 2.25*inch)
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
