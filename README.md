# ATS Inspection Report Generator (Customizable Team Version)

Streamlit web app that generates professional ATS-style NDT / borescope inspection reports. Highly customizable for different customers.

## Customization features
- Client logo upload for the cover page
- Job / Unit / Project number field
- Extra cover note
- Accent color picker
- Toggle Inspection Summary and Signoff box
- Choose 1 or 2 images per page
- Custom footer text
- Save / load customer presets (session-based)
- Company logo override
- Before/after pairs side-by-side

## Local run
```bash
pip install -r requirements.txt
streamlit run app.py
```

Default password: `ats2026`

## Deploy to Streamlit Community Cloud
1. Create a free GitHub account and public repository.
2. Upload: `app.py`, `ats_logo.jpg`, `requirements.txt`, `README.md`
3. Go to [share.streamlit.io](https://share.streamlit.io) → New app → select repo → main file `app.py`.
4. Share the URL + password with the team.

**Change password:** Streamlit Cloud → Settings → Secrets:
```toml
password = "your-new-password"
```

## Notes
- Presets are stored in the browser session (not permanently on the server).
- Images are processed only during the session and are not stored.
- PDF branding, colors, and content can be adjusted per customer before generating.
