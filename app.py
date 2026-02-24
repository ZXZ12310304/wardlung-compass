import base64
import html
import json
import os
import socket
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Response, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

from src.auth import credentials
from src.ui import nurse_app
from src.ui import patient_app
from src.ui import patient_pages
from src.ui import staff_pages

# Define absolute paths for the database, upload directories, and UI image assets
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.abspath(os.path.join(BASE_DIR, "data", "image"))
DB_PATH = os.path.abspath(os.path.join(BASE_DIR, "data", "ward_demo.db"))
BG_PATH = os.path.abspath(os.path.join(IMG_DIR, "login_background.png"))
LOGO_PATH = os.path.abspath(os.path.join(IMG_DIR, "logo.png"))
UPLOADS_DIR = os.path.abspath(os.path.join(BASE_DIR, "data", "uploads"))
DEFAULT_DEMO_PASSWORD = os.getenv("DEMO_DEFAULT_PASSWORD", "Demo@123")

# Configure default environment variables for MedGemma and ASR models (e.g., CPU fallback, token limits)
# Keep GPU headroom for MedGemma inference on 8GB-class cards.
os.environ.setdefault("MED_ASR_DEVICE", "cpu")
os.environ.setdefault("MED_ASR_USE_FP16", "1")
os.environ.setdefault("MEDSIGLIP_DEVICE", "cpu")
os.environ.setdefault("MEDGEMMA_MAX_NEW_TOKENS", "256")
os.environ.setdefault("MEDGEMMA_RETRY_MAX_NEW_TOKENS", "128")
os.environ.setdefault("MEDGEMMA_MAX_INPUT_TOKENS", "2048")
os.environ.setdefault("RAG_EVIDENCE_TOTAL_CHARS", "1500")


# Helper function: Convert local image files to Base64 data URIs for inline HTML rendering
def _b64_data_uri(path: str) -> str:
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/png;base64,{data}"
    except Exception:
        return ""


# Pre-load and convert the login background and logo images
BG_DATA = _b64_data_uri(BG_PATH)
LOGO_DATA = _b64_data_uri(LOGO_PATH)




# Global CSS stylesheet defining the layout, colors, and animations for the web UI
CSS = """
:root {
  --gray: #DDE4EE;
  --lime: #CFE67E;
  --teal: #6AB8C4;
  --section-title: #2F99A8;
  --light-teal: #C2F2F4;
  --white: #FFFFFF;
  --black: #000000;
  --login-btn: #CFE67E;
  --nav-icon-size: 20px;
}
* { box-sizing: border-box; }
html, body {
  width: 100%;
  height: 100%;
  margin: 0;
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  color: var(--black);
  background: var(--white);
}
footer, .gradio-footer, .built-with { display: none !important; visibility: hidden !important; height: 0 !important; }
.loading, .overlay, #status, .status, .status-text, .status-bar, .progress-text, .eta-bar, .queue-status,
.gradio-container .status, .gradio-container .status *, .gradio-container .queue, .gradio-container .queue *,
.gradio-container .loading-status, .gradio-container .loading-status *, .gradio-container .wrap > .status, .gradio-container .wrap > .status * {
  display: none !important;
  visibility: hidden !important;
  pointer-events: none !important;
}
.gradio-container .overlay,
.gradio-container .loading,
.gradio-container .block-overlay,
.gradio-container [data-testid="loading"],
.gradio-container [data-testid="progress"],
gradio-app [data-testid="loading"],
gradio-app [data-testid="progress"] {
  display: none !important;
  visibility: hidden !important;
  opacity: 0 !important;
  pointer-events: none !important;
}
.gradio-container.loading,
.gradio-container.loading *,
.gradio-container .wrap.loading,
.gradio-container .wrap.loading * {
  pointer-events: auto !important;
  filter: none !important;
  opacity: 1 !important;
}
gradio-app[aria-busy="true"],
gradio-app[aria-busy="true"] * {
  pointer-events: auto !important;
}
.gradio-container,
.gradio-container .main,
.gradio-container .wrap,
.gradio-container .contain,
.gradio-container .block,
.gradio-container .prose,
.gradio-container .container,
.gradio-container .container-fluid {
  max-width: 100% !important;
  width: 100% !important;
  padding: 0 !important;
  margin: 0 !important;
}
/* Login */
.login-page {
  width: 100%;
  min-height: 100vh;
  background-image: url("__LOGIN_BG__");
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  display: flex;
  flex-direction: column;
}
.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 40px 0 0 40px;
}
.login-brand img { width: 28px; height: 28px; border-radius: 50%; background: var(--white); }
.login-brand .brand-text { font-size: 22px; font-weight: 600; }
.login-brand .brand-text .compass { color: var(--lime); font-weight: 500; }
.login-content { flex: 1; display: flex; align-items: flex-start; padding-left: 140px; padding-top: 80px; }
.login-panel { width: 460px; max-width: 90vw; }
.login-label { font-size: 20px; font-weight: 500; margin-bottom: 14px; }
.login-title { font-size: 34px; font-weight: 700; margin: 0 0 36px 0; }
.input-group { display:flex; align-items:center; height:48px; background:var(--white); border:1px solid var(--gray); border-radius:6px; margin-bottom:18px; overflow:hidden; width:100%; }
.icon-box { width:48px; height:48px; display:flex; align-items:center; justify-content:center; background:#F5F7FA; border-right:1px solid var(--gray); }
.input-group input { border:none; outline:none; padding:0 14px; flex:1; font-size:16px; color:var(--black); background:transparent; height:48px; }
.input-group input::placeholder { color:#9AA3AF; }
input::placeholder, textarea::placeholder { color: #9AA3AF; }
.login-forgot { text-align:right; margin-top:-4px; margin-bottom:22px; width:100%; }
.login-forgot a { color: var(--teal); text-decoration:none; font-size:15px; font-weight:500; }
.login-btn { width:100%; height:48px; border:none; border-radius:8px; background:var(--login-btn); color:var(--black); font-size:17px; font-weight:600; cursor:pointer; }
.login-actions { display:flex; gap:10px; align-items:center; margin-top:2px; }
.login-actions .login-btn { flex:1; width:auto; }
.login-secondary-btn {
  height:48px;
  border:1px solid var(--gray);
  border-radius:8px;
  background:#FFFFFF;
  color:#0B3A44;
  font-size:16px;
  font-weight:600;
  padding:0 16px;
  cursor:pointer;
  white-space:nowrap;
}
.register-panel {
  padding:14px;
  border:1px solid var(--gray);
  border-radius:12px;
  background:#FFFFFF;
}
.register-title { font-size:16px; font-weight:700; color:#0B3A44; margin-bottom:10px; }
.register-note { font-size:13px; color:#6B7280; margin-top:8px; }
.register-row { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
.register-panel .input-group { margin-bottom:10px; }
.register-panel .input-group:last-child { margin-bottom:0; }
.register-panel select {
  width:100%;
  height:48px;
  border:none;
  outline:none;
  padding:0 14px;
  font-size:16px;
  background:transparent;
  color:#111827;
}
.register-actions { display:flex; gap:10px; margin-top:12px; }
.register-actions .login-btn, .register-actions .login-secondary-btn { flex:1; width:auto; }
.register-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(10, 26, 40, 0.45);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 999;
  padding: 20px;
}
.register-modal {
  width: 560px;
  max-width: 96vw;
  border-radius: 16px;
  background: #FFFFFF;
  box-shadow: 0 22px 56px rgba(10, 26, 40, 0.24);
  border: 1px solid #DDE4EE;
}
.register-modal-head {
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:14px 16px 0;
}
.register-close {
  border:1px solid #DDE4EE;
  background:#FFFFFF;
  border-radius:10px;
  width:34px;
  height:34px;
  font-size:20px;
  line-height:1;
  color:#0B3A44;
  cursor:pointer;
}
.register-modal-body { padding: 8px 16px 16px; }
.icon { width: 18px; height: 18px; stroke: #9AA3AF; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.icon.fill { fill: #9AA3AF; stroke: none; }
.icon-box .icon { width: 18px; height: 18px; }
.nav-item .icon { margin-right: 6px; }
.quick-card .icon { width: 64px; height: 64px; stroke: #6AB8C4; }
.quick-card .icon.fill { fill: #6AB8C4; }
.quick-card .qc-icon { display:flex; align-items:center; }
.quick-card .qc-text { color: #0B3A44; font-weight: 500; }
.care-card .actions .icon { width: 14px; height: 14px; }
.logout .icon { margin-right: 6px; }
.emoji { font-family: "Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji", sans-serif; font-size: 16px; line-height: 1; }
.icon-box .emoji { font-size: 18px; color: #9AA3AF; }
.nav-item .emoji { width: 18px; display: inline-flex; justify-content: center; margin-right: 6px; }
.quick-card .emoji { color: #6AB8C4; margin-right: 6px; }
.care-card .actions .emoji { font-size: 14px; }
.logout .emoji { margin-right: 6px; }
/* Dashboard */
.dash-page {
  min-height: 100vh;
  display: flex;
  gap: 28px;
  padding: 28px;
  background: radial-gradient(1200px 800px at 80% 10%, rgba(194,242,244,0.6), transparent 60%),
              radial-gradient(900px 600px at 85% 70%, rgba(106,184,196,0.25), transparent 60%),
              #F6F9FC;
}
.sidebar {
  width: 260px;
  background: var(--white);
  position: sticky;
  top: 24px;
  align-self: stretch;
  height: calc(100vh - 56px);
  box-shadow: 0 8px 30px rgba(7, 23, 43, 0.08);
  padding: 24px 18px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.sidebar .brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 6px 8px 6px;
}
.sidebar .brand img { width: 28px; height: 28px; border-radius: 50%; }
.sidebar .brand .brand-text { font-size: 16px; font-weight: 700; }
.sidebar .brand .brand-text .compass { color: #CFE67E; }
.nav { display: flex; flex-direction: column; gap: 8px; }
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 12px;
  color: #1B2B3A;
  font-weight: 600;
  cursor: pointer;
}
.nav-item.active {
  background: rgba(106,184,196,0.35);
  color: #0B3A44;
}
.nav-item svg { width: var(--nav-icon-size); height: var(--nav-icon-size); }
.sidebar .profile {
  margin-top: auto;
  padding: 14px;
  border-radius: 16px;
  background: #F4F7FB;
  display: flex;
  align-items: center;
  gap: 12px;
}
.profile img { width: 52px; height: 52px; border-radius: 50%; }
.profile .name { font-weight: 700; }
.profile .role { color: #6B7280; font-size: 13px; }
.logout {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #1B2B3A;
  font-weight: 600;
  cursor: pointer;
}
.main {
  flex: 1;
  padding: 28px 32px;
  overflow-y: auto;
  height: calc(100vh - 56px);
  position: relative;
}
.main.font-large { font-size: 18px; }
.header-title { font-size: 32px; font-weight: 800; margin-bottom: 4px; }
.header-sub { color: #6B7280; margin-bottom: 22px; }
.card-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.card {
  background: var(--white);
  border-radius: 16px;
  padding: 16px 18px;
  box-shadow: 0 8px 20px rgba(18, 38, 63, 0.08);
}
.card h4 { margin: 0 0 8px 0; font-size: 16px; }
.status-pill { display: inline-flex; align-items: center; gap: 8px; font-weight: 700; }
.status-pill.complete { color: #6ABF4B; }
.status-pill.incomplete { color: #9AA3AF; }
.link { color: var(--teal); font-weight: 600; cursor: pointer; }
.care-card {
  margin-top: 16px;
  background: var(--white);
  border-radius: 18px;
  padding: 18px 20px;
  box-shadow: 0 12px 26px rgba(18, 38, 63, 0.1);
  border: 2px solid rgba(106,184,196,0.25);
}
.care-card h3 { margin: 0 0 12px 0; }
.care-card .actions { display:flex; align-items:center; gap:8px; position: absolute; right: 18px; top: 16px; }
.care-card .actions button { background: #E6F4F6; border: none; padding: 6px 10px; border-radius: 10px; font-weight: 600; cursor: pointer; color: #0B4B57; }
.care-card .actions .icon-btn { width: 30px; height: 30px; display:flex; align-items:center; justify-content:center; }
.care-card ul { margin: 0 0 12px 18px; padding: 0; }
.care-card .view-all { text-align: right; font-weight: 600; color: var(--teal); cursor: pointer; }
.quick-row { margin-top: 16px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.quick-card { background: var(--white); border-radius: 16px; padding: 16px; box-shadow: 0 8px 18px rgba(18, 38, 63, 0.08); display: flex; flex-direction: column; gap: 10px; }
.quick-card .q-title { font-weight: 700; }
/* Daily Check */
.daily-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.daily-header-title { font-size: 28px; font-weight: 800; }
.daily-header-sub { color: #6B7280; margin-top: -6px; }
.daily-check-card {
  max-width: 860px;
  width: 100%;
  align-self: center;
  background: var(--white);
  border-radius: 18px;
  box-shadow: 0 12px 26px rgba(18, 38, 63, 0.12);
  padding: 22px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.daily-progress {
  display: flex;
  align-items: center;
  gap: 16px;
  font-weight: 600;
  color: #6B7280;
}
.progress-bar {
  flex: 1;
  height: 8px;
  background: var(--light-teal);
  border-radius: 999px;
  overflow: hidden;
}
.progress-bar .fill {
  height: 100%;
  background: var(--teal);
  border-radius: 999px;
}
.progress-pct { min-width: 48px; text-align: right; color: #6B7280; }
.section-title { font-size: 20px; font-weight: 700; margin-top: 4px; }
.section-sub { font-size: 14px; color: #6B7280; margin-top: -10px; }
.dc-content { display:flex; flex-direction:column; gap:14px; }
.dc-section-title { font-size:16px; font-weight:700; margin-top:4px; }
.dc-radio {
  display: block;
  cursor: pointer;
}
.dc-radio input { display: none; }
.dc-radio-pill {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid #DDE4EE;
  border-radius: 999px;
  padding: 10px 14px;
  margin-top: 12px;
  transition: all 0.15s ease;
}
.dc-radio input:checked + .dc-radio-pill {
  background: var(--light-teal);
  border-color: var(--teal);
}
.dc-radio-icon {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid #B9C3CF;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: transparent;
  font-size: 14px;
}
.dc-radio input:checked + .dc-radio-pill .dc-radio-icon {
  background: var(--teal);
  border-color: var(--teal);
  color: #FFFFFF;
}
.dc-chips { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; margin-top: 10px; }
.dc-chip { display: block; cursor: pointer; }
.dc-chip input { display: none; }
.dc-chip-pill {
  border: 1px solid #DDE4EE;
  border-radius: 999px;
  padding: 8px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: all 0.15s ease;
}
.dc-chip-check {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  border: 1.5px solid #B9C3CF;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: transparent;
}
.dc-chip input:checked + .dc-chip-pill {
  background: var(--light-teal);
  border-color: var(--teal);
}
.dc-chip input:checked + .dc-chip-pill .dc-chip-check {
  border-color: var(--teal);
  background: var(--lime);
  color: #1B2B3A;
}
.dc-slider { width: 100%; }
.dc-slider input[type="range"] { width: 100%; accent-color: var(--teal); }
.dc-slider .value { color: #6B7280; font-size: 14px; margin-top: 6px; }
.dc-pills { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; margin-top: 10px; }
.dc-pill { display: block; cursor: pointer; }
.dc-pill input { display: none; }
.dc-pill span {
  display: block;
  text-align: center;
  padding: 8px 10px;
  border-radius: 999px;
  border: 1px solid #DDE4EE;
}
.dc-pill input:checked + span {
  background: var(--light-teal);
  border-color: var(--teal);
  color: #0B3A44;
}
.dc-textarea {
  width: 100%;
  min-height: 120px;
  border: 1px solid #DDE4EE;
  border-radius: 12px;
  padding: 12px 14px;
  font-size: 15px;
  font-family: inherit;
}
.dc-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-top: 1px solid #EEF2F6;
  padding-top: 16px;
}
.dc-btn {
  border: 1px solid #DDE4EE;
  background: var(--white);
  border-radius: 999px;
  padding: 10px 18px;
  font-weight: 600;
  cursor: pointer;
}
.dc-btn:disabled { color: #B0B8C2; border-color: #EEF2F6; cursor: not-allowed; }
.dc-next { background: var(--lime); border: none; color: #0B3A44; }
.dc-save { color: var(--teal); font-weight: 600; cursor: pointer; }
@media (max-width: 1100px) {
  .dc-chips { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .dc-pills { grid-template-columns: repeat(2, minmax(0,1fr)); }
}
/* Care Cards */
.care-header { display:flex; align-items:flex-end; justify-content:space-between; }
.care-header h1 { font-size: 30px; margin:0; }
.care-sub { color:#6B7280; margin-top:4px; }
.care-topbar { margin-top: 16px; display:flex; align-items:center; gap:12px; }
.care-search { flex:1; position: relative; }
.care-search input { width:100%; padding:12px 14px 12px 38px; border-radius:12px; border:1px solid #DDE4EE; background:#FFFFFF; font-size:15px; }
.care-search .icon { position:absolute; left:12px; top:50%; transform:translateY(-50%); width:18px; height:18px; stroke:#9AA3AF; }
.care-sort { color:#6B7280; font-weight:600; }
.care-search-btn { border:1px solid #DDE4EE; background:#FFFFFF; border-radius:10px; padding:10px 14px; font-weight:600; cursor:pointer; color:#0B3A44; }
.care-grid { margin-top: 18px; display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:16px; }
.care-card-item { background:#FFFFFF; border-radius:16px; padding:16px; box-shadow:0 8px 18px rgba(18,38,63,0.08); border:1px solid #EEF2F6; cursor:pointer; }
.care-pill { display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px; border:1px solid #6AB8C4; color:#0B3A44; font-weight:600; font-size:12px; background:#E8F6F8; }
.care-title { margin:10px 0 8px; font-size:18px; font-weight:700; }
.care-bullets { color:#374151; font-size:14px; line-height:1.4; list-style: none; padding-left: 0; margin: 6px 0 8px 0; }
.care-bullets li { position: relative; padding-left: 14px; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.care-bullets li::before { content: "•"; position:absolute; left:0; top:0; color:#111111; }
.care-date-row { display:flex; align-items:center; justify-content:space-between; margin-top:10px; color:#6B7280; font-size:12px; }
.care-status-dot { width:10px; height:10px; border-radius:50%; background:var(--lime); display:inline-block; }
.care-status-check { width:16px; height:16px; display:inline-flex; align-items:center; justify-content:center; background:#6AB8C4; color:#fff; border-radius:50%; font-size:11px; }
.care-modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.25); display:flex; align-items:center; justify-content:center; z-index: 200; }
.care-modal { width: 640px; max-width: 92vw; background:#FFFFFF; border-radius:16px; box-shadow:0 18px 40px rgba(18,38,63,0.2); overflow:hidden; padding:0; }
.care-modal-scroll { max-height: 80vh; overflow-y: auto; padding:18px 20px; scrollbar-gutter: stable; }
.care-modal-scroll::-webkit-scrollbar { width: 12px; }
.care-modal-scroll::-webkit-scrollbar-track { background: transparent; margin: 8px 0; }
.care-modal-scroll::-webkit-scrollbar-thumb { background:#C7D2E0; border-radius:999px; border:3px solid transparent; background-clip:content-box; }
.care-modal-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
.care-modal h3 { margin:0 0 4px; }
.care-modal-date { color:#6B7280; font-size:13px; }
.care-modal-tts {
  border:none;
  background:#E6F4F6;
  color:#0B4B57;
  border-radius:10px;
  width:34px;
  height:34px;
  display:flex;
  align-items:center;
  justify-content:center;
  cursor:pointer;
  flex:0 0 auto;
}
.care-modal-tts .icon { width:16px; height:16px; stroke:#0B4B57; }
.care-modal-tts:hover { filter:brightness(0.97); }
.care-modal ul { margin: 10px 0 12px 18px; }
.care-focus { margin-top: 8px; font-weight: 600; color: #0B3A44; }
.care-section { margin-top: 12px; }
.care-section-title { font-size: 13px; font-weight: 700; color: #6B7280; letter-spacing: 0.4px; }
.care-modal-actions { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top: 16px; }
.care-action { padding:10px 18px; border-radius:14px; border:1px solid #DDE4EE; background:#FFFFFF; cursor:pointer; font-weight:600; font-size:15px; }
.care-action-primary { background:var(--lime); border-color:var(--lime); color:#0B3A44; }
.care-action-delete { background:var(--light-teal); border-color:var(--teal); color:#0B3A44; }
.care-action-secondary { background:#ECEFF3; border-color:#ECEFF3; color:#6B7280; }
/* Chat */
.chat-layout { display:grid; grid-template-columns: 1.6fr 1fr; gap:18px; }
.chat-panel { background:#FFFFFF; border-radius:16px; box-shadow:0 10px 22px rgba(18,38,63,0.08); padding:16px; display:flex; flex-direction:column; min-height: calc(100vh - 56px); }
.chat-title { font-size:20px; font-weight:700; display:flex; align-items:center; gap:10px; }
.chat-bubbles { flex:1; display:flex; flex-direction:column; gap:10px; margin-top:12px; overflow-y:auto; }
.chat-empty { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; color:#0B3A44; gap:10px; padding:20px 10px; }
.chat-empty-title { font-size:22px; font-weight:800; }
.chat-empty-sub { color:#6B7280; }
.chat-suggestions { display:flex; flex-wrap:wrap; gap:10px; justify-content:center; }
.chat-suggestions button { border:1px solid #DDE4EE; background:#FFFFFF; color:#0B3A44; padding:8px 12px; border-radius:999px; cursor:pointer; font-weight:600; }
.chat-suggestions button:hover { background:#F4F7FB; }
.chat-thinking { background:#FFFFFF; border:1px dashed #DDE4EE; border-radius:14px; padding:10px 12px; color:#0B3A44; display:none; }
.chat-thinking .thinking-title { font-weight:700; margin-bottom:6px; }
.thinking-steps { display:flex; flex-direction:column; gap:4px; font-size:13px; color:#6B7280; }
.thinking-step { opacity:0.45; }
.thinking-step.step1 { animation: stepPulse 4s infinite; animation-delay:0s; }
.thinking-step.step2 { animation: stepPulse 4s infinite; animation-delay:1s; }
.thinking-step.step3 { animation: stepPulse 4s infinite; animation-delay:2s; }
.thinking-step.step4 { animation: stepPulse 4s infinite; animation-delay:3s; }
.thinking-bar { height:6px; background:#DDE4EE; border-radius:999px; overflow:hidden; margin-top:8px; }
.thinking-bar span { display:block; width:40%; height:100%; background:#6AB8C4; animation: thinkingMove 1.6s linear infinite; }
@keyframes thinkingMove { 0% { transform: translateX(-60%); } 100% { transform: translateX(160%); } }
@keyframes stepPulse { 0%, 20% { opacity:1; color:#0B3A44; } 21%, 100% { opacity:0.45; color:#6B7280; } }
.bubble { max-width: 70%; padding:10px 12px; border-radius:12px; box-shadow:0 4px 10px rgba(18,38,63,0.08); }
.bubble.user { align-self:flex-end; background:#C2F2F4; }
.bubble.assistant { align-self:flex-start; background:#FFFFFF; border:1px solid #DDE4EE; }
.bubble .bubble-text { white-space:pre-wrap; }
.bubble-with-tts { position:relative; padding-right:44px; }
.chat-tts-btn {
  position:absolute;
  top:8px;
  right:8px;
  width:26px;
  height:26px;
  border:none;
  border-radius:8px;
  background:#E6F4F6;
  color:#0B4B57;
  display:flex;
  align-items:center;
  justify-content:center;
  cursor:pointer;
}
.chat-tts-btn .icon { width:14px; height:14px; stroke:#0B4B57; }
.chat-tts-btn:hover { filter:brightness(0.97); }
.bubble.chat-thinking { border:1px dashed #DDE4EE; }
.bubble.chat-thinking .thinking-title { font-weight:700; margin-bottom:6px; }
.bubble.chat-thinking .thinking-steps { display:flex; flex-direction:column; gap:4px; font-size:13px; color:#6B7280; }
.bubble.chat-thinking .thinking-step { opacity:0.45; }
.bubble.chat-thinking .thinking-step.step1 { animation: stepPulse 4s infinite; animation-delay:0s; }
.bubble.chat-thinking .thinking-step.step2 { animation: stepPulse 4s infinite; animation-delay:1s; }
.bubble.chat-thinking .thinking-step.step3 { animation: stepPulse 4s infinite; animation-delay:2s; }
.bubble.chat-thinking .thinking-step.step4 { animation: stepPulse 4s infinite; animation-delay:3s; }
.bubble.chat-thinking .thinking-bar { height:6px; background:#DDE4EE; border-radius:999px; overflow:hidden; margin-top:8px; }
.bubble.chat-thinking .thinking-bar span { display:block; width:40%; height:100%; background:#6AB8C4; animation: thinkingMove 1.6s linear infinite; }
@keyframes thinkingMove { 0% { transform: translateX(-60%); } 100% { transform: translateX(160%); } }
@keyframes stepPulse { 0%, 20% { opacity:1; color:#0B3A44; } 21%, 100% { opacity:0.45; color:#6B7280; } }
.chat-input-bar { display:flex; align-items:center; gap:8px; border:1px solid #DDE4EE; border-radius:12px; padding:8px 10px; margin-top:10px; }
.chat-input-bar input { flex:1; border:none; outline:none; font-size:15px; }
.chat-btn { height:40px; min-width:64px; padding:0 12px; border-radius:10px; border:1px solid #DDE4EE; background:#FFFFFF; cursor:pointer; display:flex; align-items:center; justify-content:center; font-weight:600; color:#0B3A44; }
.chat-btn .icon { width: 22px; height: 22px; }
.chat-btn svg { width: 22px; height: 22px; }
.chat-btn.recording { border-color:#6AB8C4; box-shadow:0 0 0 2px rgba(106,184,196,0.2); }
.chat-send { background:var(--lime); border:none; font-weight:700; padding:0 16px; height:40px; border-radius:10px; cursor:pointer; }
.chat-note { color:#6B7280; font-size:12px; margin-top:6px; }
.safety-panel { display:flex; flex-direction:column; gap:12px; min-height: calc(100vh - 56px); }
.safety-card { background:#FFFFFF; border-radius:16px; padding:14px 16px; box-shadow:0 8px 18px rgba(18,38,63,0.08); }
.safety-panel { display:flex; flex-direction:column; gap:12px; min-height: calc(100vh - 200px); }
.safety-card { background:#FFFFFF; border-radius:16px; box-shadow:0 8px 18px rgba(18,38,63,0.08); padding:14px; }
.safety-card h4 { margin:0 0 8px; }
/* Inbox */
.inbox-page { display:flex; flex-direction:column; }
.inbox-layout { display:grid; grid-template-columns: 1.2fr 1.4fr; gap:16px; margin-top: 8px; }
.inbox-list, .inbox-detail { background:#FFFFFF; border-radius:16px; box-shadow:0 8px 18px rgba(18,38,63,0.08); padding:16px; min-height: calc(100vh - 200px); display:flex; flex-direction:column; }
.inbox-detail .detail-actions { margin-top: auto; padding-top: 16px; }
.inbox-tabs { display:flex; gap:18px; border-bottom:1px solid #EEF2F6; padding-bottom:8px; margin-bottom:12px; font-size:14px; }
.inbox-tab { cursor:pointer; font-weight:600; color:#6B7280; padding-bottom:6px; }
.inbox-tab.active { color:#0B3A44; border-bottom:3px solid #6AB8C4; }
.inbox-search { position: relative; margin-bottom:12px; }
.inbox-search input { width:100%; padding:10px 12px 10px 34px; border-radius:12px; border:1px solid #DDE4EE; }
.inbox-search .icon { position:absolute; left:10px; top:50%; transform:translateY(-50%); width:16px; height:16px; stroke:#9AA3AF; }
.inbox-search-btn { border:1px solid #DDE4EE; background:#FFFFFF; border-radius:10px; padding:8px 12px; font-weight:600; cursor:pointer; color:#0B3A44; margin-bottom:12px; }
.msg-item { border:1px solid #EEF2F6; border-radius:12px; padding:10px 12px; margin-bottom:10px; cursor:pointer; display:grid; grid-template-columns: 140px 1fr auto; gap:12px; align-items:center; font-size:14px; }
.msg-item .title { font-weight:600; font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.msg-item .meta { color:#6B7280; font-size:12px; white-space:nowrap; }
.msg-dot { width:10px; height:10px; border-radius:50%; background:var(--lime); }
.detail-title { font-size:20px; font-weight:700; margin-bottom:6px; }
.detail-meta { color:#6B7280; margin-bottom:12px; font-size:13px; }
.detail-actions { margin-top:16px; display:flex; gap:12px; }
.detail-actions-end { justify-content:flex-end; }
.delete-btn-themed {
  border:1px solid var(--teal);
  background: var(--light-teal);
  color:#0B3A44;
  border-radius:999px;
  padding:8px 14px;
  font-weight:700;
  cursor:pointer;
}
.delete-btn-themed:hover { filter: brightness(0.98); }
/* Settings */
.settings-grid { display:grid; grid-template-columns: 1fr 1fr; gap:16px; }
.settings-grid + .settings-grid { margin-top: 16px; }
.settings-card { background:#FFFFFF; border-radius:16px; box-shadow:0 8px 18px rgba(18,38,63,0.08); padding:16px; }
.settings-card h4 { margin:0 0 8px; }
.settings-field { margin-top:10px; }
.settings-field input, .settings-field select { width:100%; padding:10px 12px; border-radius:12px; border:1px solid #DDE4EE; }
.settings-field input:not([readonly]) {
  padding-right:36px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%239AA3AF' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 20h9'/%3E%3Cpath d='M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z'/%3E%3C/svg%3E");
  background-repeat:no-repeat;
  background-position:right 12px center;
  background-size:14px 14px;
}
.settings-field input[type='password'] {
  padding-right:12px;
  background-image:none;
}
.settings-hint { margin-top:8px; color:#9AA3AF; font-size:13px; line-height:1.35; }
.settings-toggle { display:flex; gap:8px; margin-top:6px; }
.settings-toggle button { border:1px solid #DDE4EE; border-radius:999px; padding:8px 12px; background:#FFFFFF; cursor:pointer; }
.settings-toggle .active { background:#6AB8C4; color:#FFFFFF; border-color:#6AB8C4; }
.settings-save { background:var(--lime); border:none; padding:10px 14px; border-radius:999px; font-weight:700; cursor:pointer; }
.dash-nurse-call { margin-top:10px; }
.nurse-call-fab-wrap {
  position: absolute;
  top: 28px;
  right: 32px;
  margin: 0;
  z-index: 15;
}
.nurse-call-fab {
  border:1px solid #FCA5A5;
  background:#FEE2E2;
  color:#7F1D1D;
  border-radius:999px;
  padding:6px 12px;
  font-weight:700;
  font-size:13px;
  cursor:pointer;
}
.nurse-call-modal-backdrop { z-index: 260; }
.nurse-call-modal { width: 560px; max-width: 92vw; }
.nurse-call-form { margin-top:8px; display:flex; flex-direction:column; gap:8px; }
.nurse-call-textarea {
  width:100%;
  min-height:78px;
  border:1px solid #DDE4EE;
  border-radius:12px;
  padding:10px 12px;
  font-size:14px;
  line-height:1.4;
  font-family:inherit;
  resize:vertical;
  background:#FFFFFF;
}
.nurse-call-textarea:focus {
  outline:none;
  border-color:#6AB8C4;
  box-shadow:0 0 0 2px rgba(106,184,196,0.16);
}
.nurse-call-attach-row { display:flex; gap:8px; flex-wrap:wrap; }
.urgent-btn {
  border:1px solid #FCA5A5;
  background:#FEE2E2;
  color:#7F1D1D;
  border-radius:999px;
  padding:8px 12px;
  font-weight:700;
  cursor:pointer;
}
.settings-links a { color:#6AB8C4; text-decoration:none; display:inline-flex; align-items:center; gap:6px; }
.avatar-upload { display:flex; align-items:center; gap:12px; }
.avatar-preview { width:56px; height:56px; border-radius:50%; background:#F4F7FB; display:flex; align-items:center; justify-content:center; overflow:hidden; border:1px solid #DDE4EE; }
.avatar-preview img { width:100%; height:100%; object-fit:cover; }
.avatar-input { position: relative; }
.avatar-file { position: absolute; opacity: 0; pointer-events: none; width: 1px; height: 1px; }
.upload-btn { display:inline-flex; align-items:center; justify-content:center; background:#FFFFFF; border:1px solid #DDE4EE; color:#0B3A44; padding:10px 14px; border-radius:999px; font-weight:600; cursor:pointer; }
.upload-btn:hover { background:#F4F7FB; }
@media (max-width: 1100px) {
  .care-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
  .chat-layout { grid-template-columns: 1fr; }
  .inbox-layout { grid-template-columns: 1fr; }
  .settings-grid { grid-template-columns: 1fr; }
  .nurse-call-fab-wrap {
    position: static;
    display: flex;
    justify-content: flex-end;
    margin-bottom: 10px;
  }
}
.progress-ring { width: 60px; height: 60px; }
.toast {
  position: fixed;
  left: 50%;
  bottom: 40px;
  transform: translateX(-50%);
  background: rgba(132, 132, 132, 0.6);
  color: var(--black);
  padding: 12px 20px;
  border-radius: 12px;
  font-size: 14px;
  z-index: 10;
  display: none;
}
.toast.show { display: block; animation: toastFade 3s forwards; }
.mini-toast {
  position: fixed;
  left: 50%;
  bottom: 24px;
  transform: translateX(-50%);
  background: rgba(55, 65, 81, 0.92);
  color: #F9FAFB;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 13px;
  z-index: 9999;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
}
.mini-toast.show { opacity: 1; }
@keyframes toastFade {
  0% { opacity: 1; }
  80% { opacity: 1; }
  100% { opacity: 0; }
}
.hidden-trigger { display: none !important; }
.hidden-uploader { position: fixed; left: -10000px; top: -10000px; width: 1px; height: 1px; opacity: 0; }
@media (max-width: 1100px) {
  .card-row { grid-template-columns: 1fr; }
  .quick-row { grid-template-columns: repeat(2, minmax(0,1fr)); }
}

/* Nurse UI */
.staff-toolbar { display:flex; flex-wrap:wrap; gap:12px; align-items:center; background:#FFFFFF; padding:12px 14px; border-radius:16px; box-shadow:0 8px 18px rgba(18,38,63,0.08); margin-bottom:16px; }
.toolbar-item select { border:1px solid #DDE4EE; border-radius:10px; padding:8px 10px; font-weight:600; color:#0B3A44; background:#FFFFFF; }
.toolbar-fixed {
  border:1px solid #DDE4EE;
  border-radius:10px;
  padding:8px 12px;
  font-weight:700;
  color:#0B3A44;
  background:#F8FAFC;
  white-space:nowrap;
}
.toolbar-search { flex:1 1 clamp(200px, 24vw, 340px); min-width:180px; }
.toolbar-search input { width:100%; padding:9px 12px; border-radius:12px; border:1px solid #DDE4EE; }
.toolbar-filters { display:flex; gap:8px; flex-wrap:wrap; flex:0 1 auto; min-width:0; }
.chip { border:1px solid #DDE4EE; background:#FFFFFF; border-radius:999px; padding:6px 14px; font-weight:600; cursor:pointer; color:#0B3A44; white-space:nowrap; flex:0 0 auto; }
.chip.active { background: var(--light-teal); border-color: var(--teal); }
.toolbar-refresh { border:1px solid #DDE4EE; background:#CFE67E; border-radius:999px; padding:8px 14px; font-weight:700; cursor:pointer; white-space:nowrap; margin-left:auto; flex:0 0 auto; }

.dash-page .main { overflow-x: hidden; }
.ward-grid { display:grid; grid-template-columns: minmax(0, 1fr); gap:16px; align-items:start; }
.ward-side { display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:16px; align-items:start; }
.ward-table { overflow-x: hidden; }
.ward-table .ward-head, .ward-row { display:grid; grid-template-columns: 0.55fr 1.35fr 1fr 0.95fr 0.95fr 1.1fr 0.75fr; gap:10px; align-items:center; font-size:14px; }
.ward-head > div, .ward-row > div { min-width:0; }
.ward-head { font-weight:700; color:#6B7280; padding-bottom:8px; border-bottom:1px solid #EEF2F6; }
.card-title { color:var(--section-title); font-weight:700; font-size:18px; }
.subtle-note { color:#9AA3AF; font-size:12px; margin-top:4px; margin-bottom:8px; }
.ward-row { padding:10px 0; border-bottom:1px solid #EEF2F6; }
.ward-row.empty { color:#6B7280; }
.ward-row .mono { white-space:normal; word-break:break-word; }
.risk-badge { display:inline-flex; align-items:center; gap:6px; border-radius:999px; padding:4px 10px; font-weight:700; font-size:12px; white-space:nowrap; }
.risk-stable { border:1px solid var(--teal); color:#0B3A44; }
.risk-attention { border:1px solid var(--lime); color:#0B3A44; }
.risk-high { border:1px solid #0B3A44; color:#0B3A44; font-weight:800; }
.pending-card .pending-item { border-bottom:1px solid #EEF2F6; padding:10px 0; display:grid; grid-template-columns: 96px 1fr auto; gap:8px; align-items:start; }
.pending-card .pending-item:last-child { border-bottom:0; }
.pending-time { font-size:12px; color:#6B7280; }
.pending-summary { font-weight:600; line-height:1.35; }
.pending-tags { display:flex; gap:6px; flex-wrap:wrap; }
.pending-card .pending-item .pill-btn { grid-column:1 / -1; justify-self:start; margin-top:2px; }
.tasks-card .task-item { display:flex; gap:8px; align-items:center; padding:6px 0; }
.tasks-card .task-item input[type='checkbox'] {
  width:16px;
  height:16px;
  accent-color: var(--lime);
}

.staff-topbar { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:16px; }
.patient-picker select { border:1px solid #DDE4EE; border-radius:12px; padding:8px 10px; font-weight:600; }
.staff-meta { color:#6B7280; font-weight:600; }
.split-cards { display:grid; grid-template-columns: 1fr 1fr; gap:16px; margin-bottom:16px; }
.doctor-patient360-stack { grid-template-columns: 1fr; }
.form-grid { display:grid; grid-template-columns: 1fr 1fr; gap:12px; }
.form-grid label { display:flex; flex-direction:column; gap:6px; font-size:13px; color:#6B7280; }
.form-grid input {
  border:1px solid #DDE4EE;
  border-radius:10px;
  padding:8px 10px;
  background:#FFFFFF !important;
  color:#111827;
  opacity:1;
}
.form-actions { margin-top:12px; }
.form-actions.right { display:flex; justify-content:flex-end; }
.vitals-mar-page .entry-cards { align-items:stretch; }
.vitals-mar-page .entry-cards > .card { display:flex; flex-direction:column; }
.vitals-mar-page .mar-card .mar-table { flex:1; }
.vitals-mar-page .mar-card .form-actions.right { margin-top:auto; padding-top:12px; }
.mar-table { display:flex; flex-direction:column; gap:6px; }
.mar-head, .mar-row { display:grid; grid-template-columns: 1.4fr 0.7fr 0.6fr 0.8fr; gap:8px; align-items:center; }
.mar-head { font-weight:700; color:#6B7280; border-bottom:1px solid #EEF2F6; padding-bottom:6px; }
.mar-row { padding:6px 0; border-bottom:1px solid #EEF2F6; }
.recent-head, .recent-row { display:grid; grid-template-columns: 120px 1fr 1fr; gap:8px; align-items:center; }
.recent-head { font-weight:700; color:#6B7280; border-bottom:1px solid #EEF2F6; padding-bottom:6px; }
.recent-row { padding:8px 0; border-bottom:1px solid #EEF2F6; }

.pill-btn { border:1px solid #DDE4EE; background:#FFFFFF; border-radius:999px; padding:6px 14px; font-weight:700; cursor:pointer; }
.pill-btn.primary { background: var(--lime); border-color: var(--lime); }
.pill-btn.save-disabled,
.pill-btn:disabled {
  background:#E5E7EB !important;
  border-color:#E5E7EB !important;
  color:#6B7280 !important;
  cursor:not-allowed !important;
}

.generate-bar { display:flex; flex-direction:column; align-items:center; justify-content:center; margin:16px 0 22px; }
.generate-btn { background: var(--lime); border:1px solid #DDE4EE; border-radius:999px; padding:10px 20px; font-weight:800; cursor:pointer; }
.generate-btn.is-busy { opacity:0.82; cursor:wait; }
.generate-status { min-height:18px; margin-top:8px; font-size:13px; color:#2B7080; visibility:hidden; }
.generate-status.show { visibility:visible; }
.generate-status.running::before {
  content:"";
  display:inline-block;
  width:10px;
  height:10px;
  margin-right:8px;
  border:2px solid #6AB8C4;
  border-top-color: transparent;
  border-radius:50%;
  vertical-align:-1px;
  animation:wlSpin 0.8s linear infinite;
}
@keyframes wlSpin { to { transform:rotate(360deg); } }
.summary-text { line-height:1.6; }
.key-changes ul, .gap-list { margin: 8px 0 0 18px; }
.assessment-summary-list li { margin: 0 0 8px; }
.generate-assessment-page .assessment-summary-list,
.generate-assessment-page .gap-list {
  margin: 8px 0 0;
  padding-left: 26px;
}
.assessment-summary-actions { margin-top: 10px; }
.quick-actions { display:flex; gap:8px; margin-top:10px; }
.trace-head, .trace-row { display:grid; grid-template-columns: 1.1fr 0.7fr 0.6fr 1.6fr; gap:8px; align-items:center; }
.trace-head { font-weight:700; color:#6B7280; border-bottom:1px solid #EEF2F6; padding-bottom:6px; }
.trace-row { padding:6px 0; border-bottom:1px solid #EEF2F6; }
.evidence-item { border:1px solid #EEF2F6; border-radius:12px; padding:10px; margin-bottom:10px; }
.evidence-title { font-weight:700; margin-bottom:4px; overflow-wrap:anywhere; word-break:break-word; line-height:1.35; }
.evidence-snippet { color:#374151; font-size:13px; }
.evidence-score { font-size:12px; color:#6B7280; margin-top:6px; }
.assessment-detail-modal { z-index: 260; }
.assessment-modal { width: 760px; max-width: 94vw; }
.assessment-meta-grid { display:grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap:10px; margin-top:12px; }
.assessment-meta-item { border:1px solid #EEF2F6; border-radius:12px; background:#FAFCFF; padding:10px; }
.assessment-detail-section { margin-top:12px; }
.assessment-detail-section ul { margin:8px 0 0 18px; }
.assessment-edit-textarea {
  width:100%;
  min-height:200px;
  margin-top:8px;
  border:1px solid #DDE4EE;
  border-radius:12px;
  padding:12px 14px;
  font-size:15px;
  line-height:1.45;
  font-family:inherit;
  resize:vertical;
}
.assessment-edit-textarea:focus {
  outline:none;
  border-color:#6AB8C4;
  box-shadow:0 0 0 2px rgba(106,184,196,0.16);
}
.assessment-modal-actions { justify-content:flex-end; flex-wrap:wrap; }
.care-action:disabled { opacity:0.55; cursor:not-allowed; }

.handover-toolbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }
.handover-generate-wrap { display:flex; flex-direction:column; align-items:flex-end; }
.range-tabs { display:flex; gap:8px; }
.tab { border:1px solid #DDE4EE; background:#FFFFFF; border-radius:999px; padding:6px 12px; font-weight:700; cursor:pointer; }
.tab.active { background: var(--light-teal); border-color: var(--teal); }
.handover-preview-list { margin: 8px 0 0; padding-left: 24px; }
.handover-preview-list li { margin: 0 0 8px; }
.handover-detail-modal { z-index: 255; }
.handover-modal { width: 760px; max-width: 94vw; }
.handover-section-list { margin: 8px 0 0; padding-left: 20px; }
.handover-section-list li { margin: 6px 0; }
.handover-forward-row { display:flex; gap:10px; margin-top:8px; align-items:center; }
.handover-forward-input {
  flex:1;
  border:1px solid #DDE4EE;
  border-radius:12px;
  padding:10px 12px;
  font-size:14px;
  font-family:inherit;
}
.handover-forward-input:focus {
  outline:none;
  border-color:#6AB8C4;
  box-shadow:0 0 0 2px rgba(106,184,196,0.16);
}
.sbar-raw {
  margin:8px 0 0;
  padding:10px 12px;
  border:1px solid #DDE4EE;
  border-radius:12px;
  background:#F8FAFC;
  font-size:13px;
  line-height:1.45;
  white-space:pre-wrap;
  overflow-wrap:anywhere;
}

.requests-layout { display:grid; grid-template-columns: 1.2fr 1fr; gap:16px; }
.requests-layout .request-status-tabs { margin-top:8px; }
.requests-layout .range-tabs { margin-bottom:12px; }
.requests-layout .source-tabs { margin-top:-4px; margin-bottom:10px; }
.request-list { margin-top:4px; }
.request-item { border:1px solid #EEF2F6; border-radius:12px; padding:10px; margin-bottom:10px; }
.request-title { font-weight:700; }
.request-meta { color:#6B7280; font-size:12px; margin-top:2px; }
.request-summary { margin-top:6px; }
.request-tags { display:flex; gap:6px; margin-top:6px; flex-wrap:wrap; }
.request-source-badge {
  background:#EEF6FF;
  color:#1D4E89;
  border:1px solid #9EC5F8;
}
.request-type-badge {
  background:#E8F6F8;
  color:#0B3A44;
  border:1px solid #6AB8C4;
}
.request-actions { display:flex; gap:8px; margin-top:8px; }
.detail-card .detail-title { font-weight:800; font-size:18px; margin-bottom:6px; }
.detail-info, .detail-status { color:#6B7280; font-size:13px; }
.detail-source { color:#2B7080; font-size:13px; margin-bottom:6px; }
.detail-type-row { min-height: 22px; margin-bottom: 4px; }
.detail-type-badge { font-size:12px; font-weight:800; }
.detail-forward-meta { color:#2B7080; font-size:13px; margin-bottom:6px; }
.detail-section { margin-top:12px; }
.request-assessment-panel { border-top:1px solid #EEF2F6; padding-top:12px; }
.request-assessment-textarea {
  width:100%;
  margin-top:8px;
  border:1px solid #DDE4EE;
  border-radius:12px;
  padding:10px 12px;
  min-height:160px;
  resize:vertical;
  font-size:14px;
  line-height:1.45;
  font-family:inherit;
}
.request-assessment-textarea:focus {
  outline:none;
  border-color:#6AB8C4;
  box-shadow:0 0 0 2px rgba(106,184,196,0.16);
}
.request-assessment-status { min-height:18px; margin-top:8px; font-size:13px; color:#2B7080; visibility:hidden; }
.request-assessment-status.show { visibility:visible; }
.attachments { display:flex; gap:12px; align-items:center; margin-top:8px; }
.audio-player { background: var(--light-teal); border-radius:12px; padding:12px; min-width:120px; text-align:center; }
.audio-player.empty { color:#6B7280; background:#EEF2F6; }
.audio-player-el { width:220px; max-width:100%; }
.image-grid { display:flex; gap:8px; flex-wrap:wrap; }
.thumb-link { display:inline-flex; width:64px; height:64px; border-radius:10px; overflow:hidden; background:#EEF2F6; border:1px solid #DDE4EE; }
.thumb-img { width:100%; height:100%; object-fit:cover; display:block; }
.attachments-empty { color:#6B7280; font-size:13px; }
.thumb { width:46px; height:46px; background:#EEF2F6; border-radius:10px; }
.auto-summary { color:#6B7280; font-size:12px; margin-top:8px; }
.detail-actions { display:flex; gap:8px; margin-top:12px; }

.patient-details .patient-main { font-size:22px; font-weight:800; }
.patient-details .patient-sub { color:#6B7280; margin-bottom:6px; }
.patient-tags { display:flex; gap:8px; margin-top:6px; }
.patient-updated { color:#6B7280; font-size:12px; margin-top:6px; }
.alert-list { margin: 0 0 0 18px; }
.vitals-mar-page .nurse-subtitle { color:var(--section-title); font-weight:800; font-size:19px; }
.vitals-mar-page .vitals-alert-list { margin:8px 0 0; padding-left:20px; list-style:disc; }
.vitals-mar-page .vitals-alert-list li { margin:8px 0; }
.generate-assessment-page .nurse-subtitle { color:var(--section-title); font-weight:800; font-size:19px; }
.generate-assessment-page .assessment-top-cards { margin-bottom:24px; }
.generate-assessment-page .stack-cards { grid-template-columns: 1fr; }
.generate-assessment-page .stack-cards .card { overflow:hidden; }
.sub-title { color:var(--section-title); font-weight:700; }
.generate-assessment-page .nurse-note-wrap { width:100%; display:flex; justify-content:center; }
.generate-assessment-page .nurse-note-input {
  display:block;
  width:98% !important;
  min-height:96px;
  margin:8px auto 0;
  padding:14px 16px;
  border:1px solid #DDE4EE;
  border-radius:14px;
  font-size:16px;
  font-family:inherit;
  line-height:1.35;
  color:#1F2937;
  background:#FFFFFF;
  resize:vertical;
  box-sizing:border-box;
}
.generate-assessment-page .nurse-note-input:focus {
  outline:none;
  border-color:#6AB8C4;
  box-shadow:0 0 0 2px rgba(106,184,196,0.16);
}
.chip-row { display:flex; gap:8px; flex-wrap:wrap; }
.generate-assessment-page .chip-row {
  row-gap: 12px;
  margin-top: 4px;
}
.generate-assessment-page .chip-row .chip {
  padding: 8px 16px;
  line-height: 1.25;
}
.attach-row { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }
.tag { display:inline-flex; align-items:center; padding:4px 10px; border-radius:999px; font-weight:700; font-size:12px; }
.tag-teal { background: var(--light-teal); color:#0B3A44; }
.tag-lime { background: var(--lime); color:#0B3A44; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }

@media (max-width: 1200px) {
  .staff-toolbar { flex-wrap: wrap; }
  .toolbar-search { flex:1 1 260px; min-width:220px; }
  .ward-grid { grid-template-columns: 1fr; }
  .ward-side { grid-template-columns: 1fr; }
  .split-cards { grid-template-columns: 1fr; }
  .requests-layout { grid-template-columns: 1fr; }
  .ward-table .ward-head, .ward-row { grid-template-columns: 0.55fr 1.25fr 1fr 0.9fr 0.9fr 1fr 0.75fr; gap:8px; }
}
@media (max-width: 900px) {
  .assessment-meta-grid { grid-template-columns: 1fr; }
}
"""
ICONS = {'user': '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 20c2-4 14-4 16 0"/></svg>', 'lock': '<svg class="icon" viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>', 'dashboard': '<svg class="icon" viewBox="0 0 24 24"><rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/></svg>', 'calendar': '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>', 'card': '<svg class="icon" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>', 'chat': '<svg class="icon" viewBox="0 0 24 24"><path d="M5 5h14a3 3 0 0 1 3 3v6a3 3 0 0 1-3 3H10l-5 4v-4H5a3 3 0 0 1-3-3V8a3 3 0 0 1 3-3z"/></svg>', 'inbox': '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 8l9 6 9-6"/></svg>', 'settings': '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>', 'mic': '<svg class="icon" viewBox="0 0 24 24"><rect x="9" y="4" width="6" height="10" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/><path d="M8 21h8"/></svg>', 'mail': '<svg class="icon" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 8l9 6 9-6"/></svg>', 'play': '<svg class="icon fill" viewBox="0 0 24 24"><polygon points="8,5 19,12 8,19"/></svg>', 'download': '<svg class="icon" viewBox="0 0 24 24"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><rect x="4" y="18" width="16" height="3" rx="1"/></svg>', 'search': '<svg class="icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>', 'paperclip': '<svg class="icon" viewBox="0 0 24 24"><path d="M21 8l-9.5 9.5a4 4 0 0 1-5.7-5.7L13 4.8a3 3 0 0 1 4.2 4.2l-7.5 7.5a2 2 0 1 1-2.8-2.8L14 6"/></svg>', 'logout': '<svg class="icon" viewBox="0 0 24 24"><path d="M10 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h5"/><path d="M17 16l4-4-4-4"/><path d="M21 12H9"/></svg>'}

USE_BACKEND_MODEL = True
CHAT_RAG_ENABLED = True
CHAT_RAG_TOP_K = 4
WARMUP_ON_START = True

credentials.configure(db_path=DB_PATH)
patient_app.configure(
    base_dir=BASE_DIR,
    db_path=DB_PATH,
    logo_data=LOGO_DATA,
    icons=ICONS,
    use_backend_model=USE_BACKEND_MODEL,
    chat_rag_enabled=CHAT_RAG_ENABLED,
    chat_rag_top_k=CHAT_RAG_TOP_K,
    warmup_on_start=WARMUP_ON_START,
)
patient_app.warmup_models()
nurse_app.configure(
    base_dir=BASE_DIR,
    db_path=DB_PATH,
    logo_data=LOGO_DATA,
    icons=ICONS,
)


_SESSIONS: Dict[str, dict] = {}
_SESSIONS_LOCK = threading.Lock()


def _get_session_id(request: Request, response: Optional[Response] = None) -> str:
    sid = request.cookies.get("wl_session")
    with _SESSIONS_LOCK:
        if not sid or sid not in _SESSIONS:
            sid = uuid.uuid4().hex
            _SESSIONS[sid] = patient_app.default_state()
    if response is not None:
        response.set_cookie("wl_session", sid, httponly=True, samesite="lax")
    return sid


def _get_state(sid: str) -> dict:
    with _SESSIONS_LOCK:
        return _SESSIONS.get(sid, patient_app.default_state())


def _set_state(sid: str, state: dict) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS[sid] = state


def _wrap_page(body_html: str) -> str:
    css = (CSS or "").replace("__LOGIN_BG__", BG_DATA)
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>WardLung Compass</title>
  <style>{css}</style>
</head>
<body>
{body_html}
<script>
var _wlDefaultPage = (function(){{
  var first = document.querySelector('.page-section[data-page]');
  return first ? first.getAttribute('data-page') : 'dashboard';
}})();
window._wl_page = window._wl_page || _wlDefaultPage;
function wlNav(page) {{
  try {{ localStorage.setItem('wl_page', page); }} catch(e) {{}}
  window._wl_page = page;
  var navs = document.querySelectorAll('.nav-item[data-page]');
  navs.forEach(function(n) {{
    if (n.getAttribute('data-page') === page) n.classList.add('active');
    else n.classList.remove('active');
  }});
  var sections = document.querySelectorAll('.page-section[data-page]');
  sections.forEach(function(s) {{
    s.style.display = (s.getAttribute('data-page') === page) ? 'block' : 'none';
  }});
  var nurseFab = document.getElementById('nurse_call_fab_wrap');
  if (nurseFab) {{
    nurseFab.style.display = (page === 'chat') ? 'none' : '';
  }}
}}
function wlShowToast(msg) {{
  if (!msg) return;
  var el = document.getElementById('wl_toast');
  if (!el) {{
    el = document.createElement('div');
    el.id = 'wl_toast';
    el.className = 'mini-toast';
    document.body.appendChild(el);
  }}
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(window._wlToastTimer);
  window._wlToastTimer = setTimeout(function() {{
    if (el) el.classList.remove('show');
  }}, 1800);
}}
window._wlTts = window._wlTts || {{ speaking: false, userStop: false }};
function wlEnsureTtsVoices(cb) {{
  if (!('speechSynthesis' in window)) {{
    cb(false);
    return;
  }}
  var voices = window.speechSynthesis.getVoices() || [];
  if (voices.length) {{
    cb(true);
    return;
  }}
  var done = false;
  var finish = function(ok) {{
    if (done) return;
    done = true;
    cb(!!ok);
  }};
  var timer = setTimeout(function() {{
    var nowVoices = window.speechSynthesis.getVoices() || [];
    finish(nowVoices.length > 0);
  }}, 300);
  var onVoices = function() {{
    clearTimeout(timer);
    var nowVoices = window.speechSynthesis.getVoices() || [];
    finish(nowVoices.length > 0);
  }};
  try {{
    if (window.speechSynthesis.addEventListener) {{
      window.speechSynthesis.addEventListener('voiceschanged', onVoices, {{ once: true }});
    }} else {{
      var prev = window.speechSynthesis.onvoiceschanged;
      window.speechSynthesis.onvoiceschanged = function() {{
        try {{ if (typeof prev === 'function') prev(); }} catch (e) {{}}
        onVoices();
      }};
    }}
  }} catch (e) {{
    clearTimeout(timer);
    finish(false);
  }}
}}
function wlPickTtsVoice(lang) {{
  if (!('speechSynthesis' in window)) return null;
  var voices = window.speechSynthesis.getVoices() || [];
  if (!voices.length) return null;
  var target = String(lang || 'en-US').toLowerCase();
  for (var i = 0; i < voices.length; i++) {{
    var vLang = String((voices[i] && voices[i].lang) || '').toLowerCase();
    if (vLang === target) return voices[i];
  }}
  for (var j = 0; j < voices.length; j++) {{
    var pLang = String((voices[j] && voices[j].lang) || '').toLowerCase();
    if (pLang.indexOf(target.split('-')[0]) === 0) return voices[j];
  }}
  return voices[0];
}}
function wlSpeakText(text) {{
  var content = String(text || '').replace(/\\s+/g, ' ').trim();
  if (!content) {{
    wlShowToast('No text to read aloud.');
    return false;
  }}
  if (!('speechSynthesis' in window) || typeof SpeechSynthesisUtterance === 'undefined') {{
    wlShowToast('TTS is not supported in this browser.');
    return false;
  }}
  if (window._wlTts.speaking) {{
    try {{
      window._wlTts.userStop = true;
      window.speechSynthesis.cancel();
    }} catch (e) {{}}
    window._wlTts.speaking = false;
    wlShowToast('Stopped.');
    return false;
  }}
  var lang = /[\u4e00-\u9fff]/.test(content) ? 'zh-CN' : 'en-US';
  var startSpeak = function(attempt) {{
    try {{
      var utter = new SpeechSynthesisUtterance(content);
      utter.lang = lang;
      var picked = wlPickTtsVoice(lang);
      if (picked) utter.voice = picked;
      utter.rate = 1.0;
      utter.pitch = 1.0;
      utter.volume = 1.0;
      utter.onstart = function() {{
        window._wlTts.speaking = true;
        window._wlTts.userStop = false;
      }};
      utter.onend = function() {{
        window._wlTts.speaking = false;
        window._wlTts.userStop = false;
      }};
      utter.onerror = function(ev) {{
        var err = String((ev && ev.error) || '').toLowerCase();
        var stoppedByUser = !!window._wlTts.userStop;
        window._wlTts.speaking = false;
        window._wlTts.userStop = false;
        if (stoppedByUser) return;
        if (attempt < 2 && (err === 'interrupted' || err === 'canceled' || err === '' || err === 'synthesis-failed')) {{
          setTimeout(function() {{ startSpeak(attempt + 1); }}, 180);
          return;
        }}
        wlShowToast('Failed to play audio.');
      }};
      window.speechSynthesis.cancel();
      if (window.speechSynthesis.resume) {{
        try {{ window.speechSynthesis.resume(); }} catch (e) {{}}
      }}
      setTimeout(function() {{
        try {{ window.speechSynthesis.speak(utter); }} catch (e) {{ wlShowToast('Failed to play audio.'); }}
      }}, attempt > 0 ? 80 : 0);
    }} catch (e) {{
      window._wlTts.speaking = false;
      window._wlTts.userStop = false;
      wlShowToast('Failed to play audio.');
    }}
  }};
  wlEnsureTtsVoices(function() {{
    startSpeak(0);
  }});
  return false;
}}
function wlEnableSaveBtn(btnId) {{
  var btn = document.getElementById(btnId);
  if (!btn) return;
  btn.disabled = false;
  btn.classList.remove('save-disabled');
}}
try {{
  var saved = localStorage.getItem('wl_page');
  if (saved && document.querySelector('.nav-item[data-page=\"' + saved + '\"]')) {{
    wlNav(saved);
  }} else {{
    wlNav(_wlDefaultPage);
  }}
}} catch(e) {{}}
async function wlApi(action, payload) {{
  var res = await fetch('/api/action', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify({{ action: action, payload: payload || {{}} }})
  }});
  var data = await res.json();
  if (data && data.html) {{
    var root = document.getElementById('app_root');
    if (root) root.innerHTML = data.html;
    var saved = null;
    try {{ saved = localStorage.getItem('wl_page'); }} catch(e) {{}}
    wlNav(saved || window._wl_page || 'dashboard');
    if (data.chat_pending) wlStartChatPoll();
  }}
  if (data && data.toast) wlShowToast(data.toast);
}}
window._wlDebounceTimers = window._wlDebounceTimers || {{}};
window._wlDebouncePending = window._wlDebouncePending || {{}};
function wlDebounceAction(action, payloadBuilder, delayMs) {{
  var key = 'debounce:' + action;
  if (window._wlDebounceTimers[key]) {{
    clearTimeout(window._wlDebounceTimers[key]);
  }}
  var commit = function() {{
    var payload = (typeof payloadBuilder === 'function') ? payloadBuilder() : (payloadBuilder || {{}});
    payload = payload || {{}};
    if (!payload.current_page) payload.current_page = window._wl_page || _wlDefaultPage || 'dashboard';
    wlApi(action, payload);
  }};
  window._wlDebouncePending[key] = commit;
  window._wlDebounceTimers[key] = setTimeout(function() {{
    var run = window._wlDebouncePending[key];
    delete window._wlDebouncePending[key];
    delete window._wlDebounceTimers[key];
    if (run) run();
  }}, delayMs || 500);
}}
function wlFlushDebounce(action, payloadBuilder) {{
  var key = 'debounce:' + action;
  if (payloadBuilder) {{
    window._wlDebouncePending[key] = function() {{
      var payload = (typeof payloadBuilder === 'function') ? payloadBuilder() : (payloadBuilder || {{}});
      payload = payload || {{}};
      if (!payload.current_page) payload.current_page = window._wl_page || _wlDefaultPage || 'dashboard';
      wlApi(action, payload);
    }};
  }}
  if (window._wlDebounceTimers[key]) {{
    clearTimeout(window._wlDebounceTimers[key]);
    delete window._wlDebounceTimers[key];
  }}
  var run = window._wlDebouncePending[key];
  delete window._wlDebouncePending[key];
  if (run) run();
}}
async function wlApiUpload(url, formData) {{
  var res = await fetch(url, {{
    method: 'POST',
    body: formData
  }});
  var data = await res.json();
  if (data && data.html) {{
    var root = document.getElementById('app_root');
    if (root) root.innerHTML = data.html;
    var saved = null;
    try {{ saved = localStorage.getItem('wl_page'); }} catch(e) {{}}
    wlNav(saved || window._wl_page || 'dashboard');
    if (data.chat_pending) wlStartChatPoll();
  }}
}}
let _wlChatTimer = null;
async function wlChatPollOnce() {{
  var res = await fetch('/api/chat_poll');
  var data = await res.json();
  if (data && data.html) {{
    var root = document.getElementById('app_root');
    if (root) root.innerHTML = data.html;
    var saved = null;
    try {{ saved = localStorage.getItem('wl_page'); }} catch(e) {{}}
    wlNav(saved || window._wl_page || 'dashboard');
  }}
  if (!data.chat_pending && _wlChatTimer) {{
    clearInterval(_wlChatTimer);
    _wlChatTimer = null;
  }}
}}
function wlStartChatPoll() {{
  if (_wlChatTimer) return;
  _wlChatTimer = setInterval(wlChatPollOnce, 800);
}}
function wlToggleRegister() {{
  var backdrop = document.getElementById('register_modal_backdrop');
  if (!backdrop) return false;
  var isOpen = backdrop.style.display === 'flex';
  backdrop.style.display = isOpen ? 'none' : 'flex';
  if (!isOpen) {{
    wlSyncRegisterRole();
  }}
  return false;
}}
function wlCloseRegister() {{
  var backdrop = document.getElementById('register_modal_backdrop');
  if (backdrop) backdrop.style.display = 'none';
  return false;
}}
function wlSyncRegisterRole() {{
  var role = (document.getElementById('register_role')?.value || 'patient').toLowerCase();
  var account = document.getElementById('register_account');
  var bedWrap = document.getElementById('register_bed_wrap');
  var ward = document.getElementById('register_ward');
  if (account) {{
    if (role === 'patient') {{
      account.placeholder = 'Patient ID (e.g., P20260210-0002)';
    }} else if (role === 'nurse') {{
      account.placeholder = 'Nurse Staff ID (e.g., N-02001)';
    }} else {{
      account.placeholder = 'Doctor Staff ID (e.g., D-02001)';
    }}
  }}
  if (bedWrap) {{
    bedWrap.style.display = (role === 'patient') ? '' : 'none';
  }}
  if (ward && !ward.value) {{
    ward.value = 'ward_a';
  }}
}}
async function wlRegister() {{
  if (window._wlRegisterBusy) return;
  var role = (document.getElementById('register_role')?.value || '').trim().toLowerCase();
  var account = (document.getElementById('register_account')?.value || '').trim();
  var password = document.getElementById('register_password')?.value || '';
  var confirm = document.getElementById('register_password2')?.value || '';
  var ward = (document.getElementById('register_ward')?.value || 'ward_a').trim();
  var bed = (document.getElementById('register_bed')?.value || '').trim();
  var name = (document.getElementById('register_name')?.value || '').trim();
  var btn = document.getElementById('register_btn');
  var toast = document.getElementById('login_toast');
  if (!role || !account) {{
    if (toast) {{ toast.textContent = 'Role and account are required.'; toast.style.display = 'block'; }}
    return;
  }}
  if (!password) {{
    if (toast) {{ toast.textContent = 'Password is required.'; toast.style.display = 'block'; }}
    return;
  }}
  if (password.length < 6) {{
    if (toast) {{ toast.textContent = 'Password must be at least 6 characters.'; toast.style.display = 'block'; }}
    return;
  }}
  if (password !== confirm) {{
    if (toast) {{ toast.textContent = 'Password confirmation does not match.'; toast.style.display = 'block'; }}
    return;
  }}
  window._wlRegisterBusy = true;
  if (btn) {{ btn.disabled = true; btn.textContent = 'Creating...'; }}
  try {{
    var res = await fetch('/api/register', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify({{
        role: role,
        account: account,
        password: password,
        confirm_password: confirm,
        ward_id: ward,
        bed_id: bed,
        name: name
      }})
    }});
    var data = await res.json();
    if (data && data.ok) {{
      var loginAccount = document.getElementById('login_account');
      var loginPassword = document.getElementById('login_password');
      if (loginAccount) loginAccount.value = data.login_account || account;
      if (loginPassword) loginPassword.value = password;
      wlCloseRegister();
      if (toast) {{
        toast.textContent = data.message || 'Account created. Please log in.';
        toast.style.display = 'block';
      }}
      return;
    }}
    if (toast) {{
      toast.textContent = (data && data.message) ? data.message : 'Failed to create account.';
      toast.style.display = 'block';
    }}
  }} catch (e) {{
    if (toast) {{
      toast.textContent = 'Network error. Please retry.';
      toast.style.display = 'block';
    }}
  }} finally {{
    window._wlRegisterBusy = false;
    if (btn) {{ btn.disabled = false; btn.textContent = 'Create account'; }}
  }}
}}
async function wlLogin() {{
  if (window._wlLoginBusy) return;
  var account = document.getElementById('login_account')?.value || '';
  var password = document.getElementById('login_password')?.value || '';
  var btn = document.getElementById('login_btn');
  window._wlLoginBusy = true;
  if (btn) {{ btn.disabled = true; btn.textContent = 'Logging in...'; }}
  try {{
    var res = await fetch('/api/login', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      credentials: 'same-origin',
      cache: 'no-store',
      body: JSON.stringify({{ account: account, password: password }})
    }});
    var data = await res.json();
    if (data && data.ok) {{
      window.location.href = '/?_=' + Date.now();
      return;
    }}
    var toast = document.getElementById('login_toast');
    if (toast) {{
      toast.textContent = data.message || 'Login failed';
      toast.style.display = 'block';
    }}
  }} catch (e) {{
    var toast = document.getElementById('login_toast');
    if (toast) {{
      toast.textContent = 'Network error. Please retry.';
      toast.style.display = 'block';
    }}
  }} finally {{
    window._wlLoginBusy = false;
    if (btn) {{ btn.disabled = false; btn.textContent = 'Log in'; }}
  }}
}}
async function wlLogout() {{
  try {{ localStorage.removeItem('wl_page'); }} catch(e) {{}}
  await fetch('/api/logout', {{ method: 'POST' }});
  window.location.reload();
}}
</script>
</body>
</html>
"""


def _render_login_html(message: str = "") -> str:
    toast_html = f'<div class="toast show" id="login_toast">{html.escape(message)}</div>' if message else '<div class="toast" id="login_toast"></div>'
    login_html = f"""
<div class="login-page">
  <div class="login-brand">
    <img src="{LOGO_DATA}" alt="WardLung Compass Logo" />
    <div class="brand-text">WardLung <span class="compass">Compass</span></div>
  </div>
  <div class="login-content">
    <div class="login-panel">
      <div class="login-label">Log in</div>
      <div class="login-title">WardLung Compass</div>
      <div class="input-group"><div class="icon-box">{ICONS.get('user','')}</div><input id="login_account" type="text" placeholder="Enter account (Patient ID or Staff ID)" /></div>
      <div class="input-group"><div class="icon-box">{ICONS.get('lock','')}</div><input id="login_password" type="password" placeholder="Enter your password" /></div>
      <div class="login-forgot"><a href="#" onclick="return false;">Forgot password?</a></div>
      <div class="login-actions">
        <button id="login_btn" class="login-btn" onclick="wlLogin(); return false;">Log in</button>
        <button id="show_register_btn" class="login-secondary-btn" onclick="return wlToggleRegister();">Create account</button>
      </div>
    </div>
  </div>
  <div id="register_modal_backdrop" class="register-modal-backdrop" onclick="if(event.target===this) return wlCloseRegister();">
    <div class="register-modal" onclick="event.stopPropagation();">
      <div class="register-modal-head">
        <div class="register-title">Create account</div>
        <button class="register-close" aria-label="Close" onclick="return wlCloseRegister();">&times;</button>
      </div>
      <div class="register-modal-body">
        <div class="register-panel">
          <div class="register-row">
            <div class="input-group">
              <div class="icon-box">{ICONS.get('dashboard','')}</div>
              <select id="register_role" onchange="wlSyncRegisterRole();">
                <option value="patient">Patient</option>
                <option value="nurse">Nurse</option>
                <option value="doctor">Doctor</option>
              </select>
            </div>
            <div class="input-group">
              <div class="icon-box">{ICONS.get('calendar','')}</div>
              <select id="register_ward">
                <option value="ward_a">Ward A</option>
                <option value="ward_b">Ward B</option>
                <option value="ward_c">Ward C</option>
              </select>
            </div>
          </div>
          <div class="input-group"><div class="icon-box">{ICONS.get('user','')}</div><input id="register_account" type="text" placeholder="Patient ID (e.g., P20260210-0002)" /></div>
          <div class="input-group"><div class="icon-box">{ICONS.get('user','')}</div><input id="register_name" type="text" placeholder="Display name (optional)" /></div>
          <div id="register_bed_wrap" class="input-group"><div class="icon-box">{ICONS.get('card','')}</div><input id="register_bed" type="text" placeholder="Bed ID (optional, e.g., A-02)" /></div>
          <div class="input-group"><div class="icon-box">{ICONS.get('lock','')}</div><input id="register_password" type="password" placeholder="Create password (min 6 chars)" /></div>
          <div class="input-group"><div class="icon-box">{ICONS.get('lock','')}</div><input id="register_password2" type="password" placeholder="Confirm password" /></div>
          <div class="register-actions">
            <button id="register_btn" class="login-btn" onclick="wlRegister(); return false;">Create account</button>
            <button class="login-secondary-btn" onclick="return wlCloseRegister();">Cancel</button>
          </div>
          <div class="register-note">Select your role and create your own account for this demo.</div>
        </div>
      </div>
    </div>
  </div>
</div>
{toast_html}
"""
    return _wrap_page(login_html)


def _build_ctx() -> dict:
    def onclick(action_id: str) -> str:
        if action_id == "do_logout":
            js = "wlLogout(); return false;"
        else:
            js = f"wlApi('{action_id}', {{}}); return false;"
        return html.escape(js, quote=True)

    def ui_onclick(action_id: str, payload: Optional[dict] = None) -> str:
        payload = payload or {}
        payload_str = json.dumps(payload, ensure_ascii=False)
        js = f"(function(){{wlApi('{action_id}', {payload_str});}})(); return false;"
        return html.escape(js, quote=True)

    def dc_onclick(action_id: str) -> str:
        js = f"""(function(){{
  var scope=document.querySelector('.daily-check-card');
  if(!scope) return false;
  var base={{}};
  try{{ base=JSON.parse(scope.getAttribute('data-answers')||'{{}}'); }}catch(e){{}}
  var diet=scope.querySelector('input[name="diet_status"]:checked');
  if(diet) base.diet_status = diet.value;
  var triggers=scope.querySelectorAll('input[name="diet_triggers"]');
  if(triggers.length) base.diet_triggers = Array.from(triggers).filter(x=>x.checked).map(x=>x.value);
  var sleep=scope.querySelector('input[name="sleep_quality"]:checked');
  if(sleep) base.sleep_quality = sleep.value;
  var hours=scope.querySelector('#sleep_hours');
  if(hours) base.sleep_hours = hours.value;
  var med=scope.querySelector('input[name="med_adherence"]:checked');
  if(med) base.med_adherence = med.value;
  var cough=scope.querySelector('input[name="symptom_cough"]:checked');
  if(cough) {{ base.symptoms = base.symptoms || {{}}; base.symptoms.cough = cough.value; }}
  var sob=scope.querySelector('input[name="symptom_sob"]:checked');
  if(sob) {{ base.symptoms = base.symptoms || {{}}; base.symptoms.sob = sob.value; }}
  var chest=scope.querySelector('input[name="symptom_chest_pain"]:checked');
  if(chest) {{ base.symptoms = base.symptoms || {{}}; base.symptoms.chest_pain = chest.value; }}
  var notes=scope.querySelector('#dc_notes');
  if(notes) base.notes_text = notes.value;
  wlApi('{action_id}', base);
}})(); return false;"""
        return html.escape(js, quote=True)

    ctx = patient_app.get_patient_ctx().copy()
    ctx.update(nurse_app.get_nurse_ctx())
    ctx.update(nurse_app.get_doctor_ctx())
    ctx.update(
        {
            "icons": ICONS,
            "logo_data": LOGO_DATA,
            "onclick": onclick,
            "ui_onclick": ui_onclick,
            "dc_onclick": dc_onclick,
        }
    )
    return ctx


def _render_app_html(state: dict) -> str:
    ctx = _build_ctx()
    if not state.get("authed"):
        return _render_login_html("")
    if state.get("role") == "patient":
        body = f"<div id='app_root'>{patient_pages.render_patient_page(state, ctx)}</div>"
        return _wrap_page(body)
    if state.get("role") == "nurse":
        body = f"<div id='app_root'>{staff_pages.render_nurse_page(state, ctx)}</div>"
        return _wrap_page(body)
    if state.get("role") == "doctor":
        body = f"<div id='app_root'>{staff_pages.render_doctor_page(state, ctx)}</div>"
        return _wrap_page(body)
    return _render_login_html("")


app = FastAPI()
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, response: Response):
    sid = _get_session_id(request, response)
    state = _get_state(sid)
    return HTMLResponse(_render_app_html(state))


@app.post("/api/register")
def api_register(request: Request, response: Response, payload: Dict[str, Any]):
    init_resp = JSONResponse({"ok": False})
    sid = _get_session_id(request, init_resp)

    def _register_resp(data: Dict[str, Any]) -> JSONResponse:
        resp = JSONResponse(data)
        resp.set_cookie("wl_session", sid, httponly=True, samesite="lax")
        return resp

    role = str(payload.get("role") or "").strip().lower()
    account = str(payload.get("account") or "").strip()
    name = str(payload.get("name") or "").strip()
    password = str(payload.get("password") or "")
    confirm_password = str(payload.get("confirm_password") or "")
    ward_id = str(payload.get("ward_id") or "ward_a").strip().lower() or "ward_a"
    bed_id = str(payload.get("bed_id") or "").strip()

    if role not in ("patient", "nurse", "doctor"):
        return _register_resp({"ok": False, "message": "Role must be patient, nurse, or doctor."})
    if not account:
        return _register_resp({"ok": False, "message": "Account is required."})
    if any(ch.isspace() for ch in account):
        return _register_resp({"ok": False, "message": "Account cannot contain spaces."})
    if len(password) < 6:
        return _register_resp({"ok": False, "message": "Password must be at least 6 characters."})
    if password != confirm_password:
        return _register_resp({"ok": False, "message": "Password confirmation does not match."})
    if not ward_id.startswith("ward_"):
        ward_id = "ward_a"

    store = patient_app.get_store()
    created_login_account = account
    try:
        if role == "patient":
            if account.upper().startswith("N-") or account.upper().startswith("D-") or "@" in account:
                return _register_resp({"ok": False, "message": "Patient ID cannot use N-/D- prefix or email format."})
            if store.get_patient(account):
                return _register_resp({"ok": False, "message": "Patient account already exists."})
            from src.store.schemas import Patient

            patient = Patient(
                patient_id=account,
                ward_id=ward_id,
                bed_id=bed_id or "A-01",
                sex=None,
                age=None,
                created_at=datetime.utcnow().isoformat(),
            )
            store.upsert_patient(patient)
            credentials.set_password(account_key=account, role="patient", raw_password=password)
            created_login_account = account
        else:
            if "@" in account:
                return _register_resp({"ok": False, "message": "Use Staff ID for doctor/nurse registration (not email)."})
            prefix = "N-" if role == "nurse" else "D-"
            normalized_staff_id = account
            if not normalized_staff_id.upper().startswith(prefix):
                normalized_staff_id = prefix + normalized_staff_id
            if normalized_staff_id.upper().startswith("N-"):
                normalized_staff_id = "N-" + normalized_staff_id[2:]
            if normalized_staff_id.upper().startswith("D-"):
                normalized_staff_id = "D-" + normalized_staff_id[2:]
            if store.get_staff_by_staff_id(normalized_staff_id):
                return _register_resp({"ok": False, "message": "Staff account already exists."})
            from src.store.schemas import StaffAccount

            staff = StaffAccount(
                staff_id=normalized_staff_id,
                role=role,
                ward_id=ward_id,
                name=name or normalized_staff_id,
                email=None,
                created_at=datetime.utcnow().isoformat(),
            )
            store.upsert_staff_account(staff)
            credentials.set_password(account_key=normalized_staff_id, role=role, raw_password=password)
            created_login_account = normalized_staff_id
    except Exception:
        return _register_resp({"ok": False, "message": "Failed to create account."})

    return _register_resp(
        {
            "ok": True,
            "message": "Account created successfully. You can now log in.",
            "login_account": created_login_account,
        }
    )


@app.post("/api/login")
def api_login(request: Request, response: Response, payload: Dict[str, Any]):
    init_resp = JSONResponse({"ok": False})
    sid = _get_session_id(request, init_resp)
    state = _get_state(sid)

    def _login_resp(data: Dict[str, Any]) -> JSONResponse:
        resp = JSONResponse(data)
        resp.set_cookie("wl_session", sid, httponly=True, samesite="lax")
        return resp

    account = (payload.get("account") or "").strip()
    password = str(payload.get("password") or "")
    if not account:
        return _login_resp({"ok": False, "message": "Account not found."})
    if not password:
        return _login_resp({"ok": False, "message": "Password is required."})
    store = patient_app.get_store()
    role = None
    ward_id = None
    patient_id = None
    staff_id = None
    account_key = None
    found_patient = False
    found_staff = False
    try:
        if "@" in account:
            staff = store.get_staff_by_email(account)
            if staff:
                role = staff.role
                staff_id = staff.staff_id
                ward_id = staff.ward_id
                found_staff = True
        elif account.upper().startswith("N-") or account.upper().startswith("D-"):
            staff = store.get_staff_by_staff_id(account)
            if not staff:
                staff = store.get_staff_by_staff_id(account.upper())
            if staff:
                role = staff.role
                staff_id = staff.staff_id
                ward_id = staff.ward_id
                found_staff = True
        else:
            patient = store.get_patient(account)
            if patient:
                role = "patient"
                patient_id = patient.patient_id
                ward_id = patient.ward_id
                found_patient = True
    except Exception:
        role = None

    if role is None and account == "demo_patient_001":
        try:
            from src.store.schemas import Patient

            patient = store.get_patient(account)
            if not patient:
                patient = Patient(
                    patient_id=account,
                    ward_id="WARD-DEMO",
                    bed_id="A-01",
                    sex=None,
                    age=None,
                    created_at=datetime.utcnow().isoformat(),
                )
                store.upsert_patient(patient)
            role = "patient"
            patient_id = account
            ward_id = getattr(patient, "ward_id", None)
            found_patient = True
        except Exception:
            role = None

    print(f"[auth] login account={account} found_patient={found_patient} found_staff={found_staff} role={role}")

    if role is None:
        return _login_resp({"ok": False, "message": "Account not found."})

    account_key = patient_id if role == "patient" else staff_id
    if not account_key:
        return _login_resp({"ok": False, "message": "Account not found."})
    credentials.ensure_default_credential(
        account_key=str(account_key or ""),
        role=str(role),
        default_password=DEFAULT_DEMO_PASSWORD,
    )
    if not credentials.verify_password(str(account_key or ""), password):
        return _login_resp({"ok": False, "message": "Invalid password."})

    state.update(
        {
            "authed": True,
            "role": role,
            "patient_id": patient_id,
            "staff_id": staff_id,
            "ward_id": ward_id,
            "current_page": "dashboard" if role == "patient" else ("doctor_dashboard" if role == "doctor" else "ward_dashboard"),
            "toast": "",
            "care_search": "",
            "care_modal_id": None,
            "highlight_card_id": None,
            "care_audio_path": None,
            "chat_history": [],
            "chat_pending": False,
            "inbox_filter": "All",
            "inbox_search": "",
            "inbox_selected_id": None,
            "settings_lang": None,
            "settings_font": None,
        }
    )
    if role == "patient":
        state = patient_app.init_daily_state(state)
    if role == "nurse":
        state = nurse_app.init_nurse_state(state, staff_id, ward_id)
    if role == "doctor":
        state = nurse_app.init_doctor_state(state, staff_id, ward_id)
    toast_msg = str(state.get("toast") or "")
    if toast_msg:
        state["toast"] = ""
    _set_state(sid, state)
    return _login_resp({"ok": True})


@app.post("/api/logout")
def api_logout(request: Request, response: Response):
    resp = JSONResponse({"ok": True})
    sid = request.cookies.get("wl_session")
    if sid:
        with _SESSIONS_LOCK:
            _SESSIONS.pop(sid, None)
    resp.delete_cookie("wl_session")
    return resp


@app.post("/api/action")
def api_action(request: Request, response: Response, payload: Dict[str, Any]):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    action = payload.get("action") or ""
    data = payload.get("payload") or {}
    data_str = json.dumps(data, ensure_ascii=False)

    role = state.get("role")
    state_only_actions = {
        "do_logout": lambda s: (patient_app.default_state(), ""),
        "do_tts": patient_app.do_tts,
    }

    patient_payload_actions = {
        "dc_prev": patient_app.dc_step_prev,
        "dc_next": patient_app.dc_step_next,
        "dc_save": patient_app.dc_save_draft,
        "dc_submit": patient_app.dc_submit_daily,
        "dc_voice": patient_app.dc_voice_toast,
        "care_open": patient_app.care_open,
        "care_close": patient_app.care_close,
        "care_mark": patient_app.care_mark,
        "care_delete": patient_app.care_delete,
        "care_tts": patient_app.care_tts,
        "care_search": patient_app.care_search,
        "care_open_latest": patient_app.care_open_latest,
        "request_nurse_now": patient_app.request_nurse_now,
        "chat_send": patient_app.chat_send,
        "chat_voice": patient_app.chat_voice,
        "chat_image": patient_app.chat_image,
        "inbox_filter": patient_app.inbox_filter,
        "inbox_search": patient_app.inbox_search,
        "inbox_select": patient_app.inbox_select,
        "inbox_ack": patient_app.inbox_ack,
        "inbox_reply": patient_app.inbox_reply,
        "inbox_delete": patient_app.inbox_delete,
        "settings_save": patient_app.settings_save,
        "settings_font": patient_app.settings_font,
        "settings_pass": patient_app.settings_pass,
    }

    nurse_payload_actions = {
        "ward_update": nurse_app.ward_update,
        "nurse_select_patient": nurse_app.nurse_select_patient,
        "task_toggle": nurse_app.task_toggle,
        "requests_filter": nurse_app.requests_filter,
        "requests_source_filter": nurse_app.requests_source_filter,
        "requests_search": nurse_app.requests_search,
        "requests_select": nurse_app.requests_select,
        "requests_update": nurse_app.requests_update,
        "requests_delete": nurse_app.requests_delete,
        "requests_generate": nurse_app.requests_generate_assessment,
        "requests_assessment_draft": nurse_app.requests_assessment_draft,
        "requests_assessment_send": nurse_app.requests_assessment_send,
        "requests_forward_doctor": nurse_app.requests_forward_doctor,
        "vitals_save": nurse_app.vitals_save,
        "mar_save": nurse_app.mar_save,
        "assessment_note": nurse_app.assessment_note,
        "assessment_generate": nurse_app.assessment_generate,
        "assessment_edit_save": nurse_app.assessment_edit_save,
        "assessment_send_patient": nurse_app.assessment_send_patient,
        "handover_generate": nurse_app.handover_generate,
        "handover_save": nurse_app.handover_save,
        "handover_forward": nurse_app.handover_forward,
        "handover_range": nurse_app.handover_range,
        "staff_settings_save": nurse_app.staff_settings_save,
        "staff_settings_pass": nurse_app.staff_settings_pass,
    }
    doctor_payload_actions = {
        "doctor_update": nurse_app.doctor_update,
        "doctor_select_patient": nurse_app.doctor_select_patient,
        "doctor_assessment_generate": nurse_app.doctor_assessment_generate,
        "doctor_note_save": nurse_app.doctor_note_save,
        "doctor_note_send": nurse_app.doctor_note_send,
        "doctor_orders_preview": nurse_app.doctor_orders_preview,
        "doctor_orders_save": nurse_app.doctor_orders_save,
        "doctor_orders_send": nurse_app.doctor_orders_send,
        "doctor_inbox_filter": nurse_app.doctor_inbox_filter,
        "doctor_inbox_source_filter": nurse_app.doctor_inbox_source_filter,
        "doctor_inbox_search": nurse_app.doctor_inbox_search,
        "doctor_inbox_select": nurse_app.doctor_inbox_select,
        "doctor_inbox_update": nurse_app.doctor_inbox_update,
        "doctor_inbox_delete": nurse_app.doctor_inbox_delete,
        "doctor_inbox_send": nurse_app.doctor_inbox_send,
        "doctor_settings_save": nurse_app.doctor_settings_save,
        "doctor_settings_pass": nurse_app.doctor_settings_pass,
        "doctor_create_patient": nurse_app.doctor_create_patient,
        "doctor_create_nurse": nurse_app.doctor_create_nurse,
    }

    if action in state_only_actions:
        state, _ = state_only_actions[action](state)
    elif role == "patient" and action in patient_payload_actions:
        fn = patient_payload_actions[action]
        if action == "chat_send":
            state, _ = fn(data_str, None, state)
        else:
            state, _ = fn(data_str, state)
    elif role == "nurse" and action in nurse_payload_actions:
        fn = nurse_payload_actions[action]
        state = fn(data_str, state)
    elif role == "doctor" and action in doctor_payload_actions:
        fn = doctor_payload_actions[action]
        state = fn(data_str, state)
    elif action.startswith("nav_"):
        page = action.replace("nav_", "")
        if role == "patient":
            state, _ = patient_app.nav_to(state, page)
        else:
            state["current_page"] = page

    toast_msg = str(state.get("toast") or "")
    if toast_msg:
        state["toast"] = ""

    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = (
        patient_pages.render_patient_page(state, ctx)
        if state.get("role") == "patient"
        else (staff_pages.render_nurse_page(state, ctx) if state.get("role") == "nurse" else staff_pages.render_doctor_page(state, ctx))
    )
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending")), "toast": toast_msg})
    _get_session_id(request, resp)
    return resp


@app.get("/api/chat_poll")
def api_chat_poll(request: Request, response: Response):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    state, _ = patient_app.poll_chat_updates(state)
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = (
        patient_pages.render_patient_page(state, ctx)
        if state.get("role") == "patient"
        else (staff_pages.render_nurse_page(state, ctx) if state.get("role") == "nurse" else staff_pages.render_doctor_page(state, ctx))
    )
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending"))})
    _get_session_id(request, resp)
    return resp


@app.post("/api/chat_image")
async def api_chat_image(
    request: Request,
    file: UploadFile = File(...),
    message: str = Form(""),
    page: str = Form(""),
):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    ext = os.path.splitext(file.filename or "")[1] or ".png"
    tmp_dir = os.path.join(BASE_DIR, "data", "tmp_images")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"chat_{uuid.uuid4().hex}{ext}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    payload = {"message": message, "current_page": page}
    state, _ = patient_app.chat_send(json.dumps(payload, ensure_ascii=False), tmp_path, state)
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = patient_pages.render_patient_page(state, ctx)
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending"))})
    _get_session_id(request, resp)
    return resp


@app.post("/api/chat_voice")
async def api_chat_voice(
    request: Request,
    file: UploadFile = File(...),
    message: str = Form(""),
    page: str = Form(""),
):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    ext = os.path.splitext(file.filename or "")[1] or ".webm"
    tmp_dir = os.path.join(BASE_DIR, "data", "tmp_audio")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"chat_{uuid.uuid4().hex}{ext}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    payload = {"message": message, "audio_path": tmp_path, "current_page": page}
    state, _ = patient_app.chat_send(json.dumps(payload, ensure_ascii=False), None, state)
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = patient_pages.render_patient_page(state, ctx)
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending"))})
    _get_session_id(request, resp)
    return resp


@app.post("/api/request_nurse_image")
async def api_request_nurse_image(
    request: Request,
    file: UploadFile = File(...),
    detail: str = Form(""),
    page: str = Form(""),
):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    ext = os.path.splitext(file.filename or "")[1] or ".png"
    tmp_dir = os.path.join(BASE_DIR, "data", "tmp_requests")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"request_img_{uuid.uuid4().hex}{ext}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    state, _ = patient_app.request_nurse_attach_image(tmp_path, detail, page, state)
    toast_msg = str(state.get("toast") or "")
    if toast_msg:
        state["toast"] = ""
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = patient_pages.render_patient_page(state, ctx)
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending")), "toast": toast_msg})
    _get_session_id(request, resp)
    return resp


@app.post("/api/request_nurse_audio")
async def api_request_nurse_audio(
    request: Request,
    file: UploadFile = File(...),
    detail: str = Form(""),
    page: str = Form(""),
):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    ext = os.path.splitext(file.filename or "")[1] or ".webm"
    tmp_dir = os.path.join(BASE_DIR, "data", "tmp_requests")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"request_audio_{uuid.uuid4().hex}{ext}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    state, _ = patient_app.request_nurse_attach_audio(tmp_path, detail, page, state)
    toast_msg = str(state.get("toast") or "")
    if toast_msg:
        state["toast"] = ""
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = patient_pages.render_patient_page(state, ctx)
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending")), "toast": toast_msg})
    _get_session_id(request, resp)
    return resp


@app.post("/api/assessment_image")
async def api_assessment_image(
    request: Request,
    file: UploadFile = File(...),
):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    ext = os.path.splitext(file.filename or "")[1] or ".png"
    tmp_dir = os.path.join(BASE_DIR, "data", "tmp_assess")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"assess_{uuid.uuid4().hex}{ext}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    if state.get("role") == "nurse":
        state = nurse_app.assessment_attach_image(tmp_path, state)
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = (
        patient_pages.render_patient_page(state, ctx)
        if state.get("role") == "patient"
        else (staff_pages.render_nurse_page(state, ctx) if state.get("role") == "nurse" else staff_pages.render_doctor_page(state, ctx))
    )
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending"))})
    _get_session_id(request, resp)
    return resp


@app.post("/api/handover_forward_image")
async def api_handover_forward_image(
    request: Request,
    file: UploadFile = File(...),
    forward_text: str = Form(""),
    target_staff_id: str = Form(""),
):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    ext = os.path.splitext(file.filename or "")[1] or ".png"
    tmp_dir = os.path.join(BASE_DIR, "data", "tmp_handover")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"handover_{uuid.uuid4().hex}{ext}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    if state.get("role") == "nurse":
        state["handover_forward_text"] = str(forward_text or "")
        state["handover_forward_target_staff_id"] = str(target_staff_id or "").strip()
        state = nurse_app.handover_forward_attach_image(tmp_path, state)
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = (
        patient_pages.render_patient_page(state, ctx)
        if state.get("role") == "patient"
        else (staff_pages.render_nurse_page(state, ctx) if state.get("role") == "nurse" else staff_pages.render_doctor_page(state, ctx))
    )
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending"))})
    _get_session_id(request, resp)
    return resp


@app.post("/api/handover_forward_audio")
async def api_handover_forward_audio(
    request: Request,
    file: UploadFile = File(...),
    forward_text: str = Form(""),
    target_staff_id: str = Form(""),
):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    ext = os.path.splitext(file.filename or "")[1] or ".webm"
    tmp_dir = os.path.join(BASE_DIR, "data", "tmp_handover")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"handover_{uuid.uuid4().hex}{ext}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    if state.get("role") == "nurse":
        state["handover_forward_text"] = str(forward_text or "")
        state["handover_forward_target_staff_id"] = str(target_staff_id or "").strip()
        state = nurse_app.handover_forward_attach_audio(tmp_path, state)
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = (
        patient_pages.render_patient_page(state, ctx)
        if state.get("role") == "patient"
        else (staff_pages.render_nurse_page(state, ctx) if state.get("role") == "nurse" else staff_pages.render_doctor_page(state, ctx))
    )
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending"))})
    _get_session_id(request, resp)
    return resp


@app.post("/api/assessment_audio")
async def api_assessment_audio(
    request: Request,
    file: UploadFile = File(...),
):
    resp = JSONResponse({})
    sid = _get_session_id(request, resp)
    state = _get_state(sid)
    ext = os.path.splitext(file.filename or "")[1] or ".webm"
    tmp_dir = os.path.join(BASE_DIR, "data", "tmp_assess")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, f"assess_{uuid.uuid4().hex}{ext}")
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)
    if state.get("role") == "nurse":
        state = nurse_app.assessment_attach_audio(tmp_path, state)
    _set_state(sid, state)
    ctx = _build_ctx()
    html_out = (
        patient_pages.render_patient_page(state, ctx)
        if state.get("role") == "patient"
        else (staff_pages.render_nurse_page(state, ctx) if state.get("role") == "nurse" else staff_pages.render_doctor_page(state, ctx))
    )
    resp = JSONResponse({"html": html_out, "chat_pending": bool(state.get("chat_pending"))})
    _get_session_id(request, resp)
    return resp


if __name__ == "__main__":
    import re
    import shutil
    import subprocess
    import time
    import uvicorn

    def _env_truthy(name: str, default: str = "1") -> bool:
        return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "y", "on")

    def _detect_lan_ip() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0] or "").strip()
        except Exception:
            return ""
        finally:
            sock.close()

    def _start_cloudflared_tunnel(port_num: int):
        cloudflared_bin = shutil.which("cloudflared")
        if not cloudflared_bin:
            return None, None, "cloudflared_not_found"
        timeout_sec = float(os.getenv("PUBLIC_TUNNEL_TIMEOUT_SEC", "15"))
        log_dir = os.path.join(BASE_DIR, "data", "tmp_tunnel")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"cloudflared_{uuid.uuid4().hex}.log")
        cmd = [
            cloudflared_bin,
            "tunnel",
            "--url",
            f"http://127.0.0.1:{port_num}",
            "--no-autoupdate",
            "--logfile",
            log_path,
            "--loglevel",
            "info",
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url_re = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
        deadline = time.monotonic() + max(3.0, timeout_sec)
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                if os.path.exists(log_path):
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                    m = url_re.search(text)
                    if m:
                        handle = {"provider": "cloudflared", "process": proc, "log_path": log_path}
                        return handle, m.group(0), None
            except Exception:
                pass
            time.sleep(0.25)
        try:
            proc.terminate()
        except Exception:
            pass
        return None, None, "cloudflared_start_failed"

    def _start_ngrok_tunnel(port_num: int):
        try:
            from pyngrok import conf, ngrok

            auth_token = str(os.getenv("NGROK_AUTHTOKEN", "")).strip()
            if auth_token:
                conf.get_default().auth_token = auth_token
            tunnel = ngrok.connect(addr=port_num, bind_tls=True)
            public_url = str(getattr(tunnel, "public_url", "") or "").strip()
            if not public_url:
                raise RuntimeError("ngrok tunnel started but public URL is empty")
            handle = {"provider": "ngrok", "tunnel": tunnel}
            return handle, public_url, None
        except Exception as exc:
            return None, None, str(exc)

    def _start_public_tunnel(port_num: int):
        if not _env_truthy("ENABLE_PUBLIC_TUNNEL", "1"):
            return None, None
        preferred = str(os.getenv("PUBLIC_TUNNEL_PROVIDER", "auto") or "auto").strip().lower()
        providers = []
        if preferred == "cloudflared":
            providers = ["cloudflared"]
        elif preferred == "ngrok":
            providers = ["ngrok"]
        else:
            providers = ["cloudflared", "ngrok"]

        for name in providers:
            if name == "cloudflared":
                handle, public_url, error = _start_cloudflared_tunnel(port_num)
                if public_url:
                    print("[startup] Public tunnel provider: cloudflared")
                    return handle, public_url
                if error not in ("cloudflared_not_found", ""):
                    print(f"[startup] cloudflared unavailable: {error}")
            elif name == "ngrok":
                handle, public_url, error = _start_ngrok_tunnel(port_num)
                if public_url:
                    print("[startup] Public tunnel provider: ngrok")
                    return handle, public_url
                if error:
                    print(f"[startup] ngrok unavailable: {error}")

        print("[startup] Public tunnel unavailable.")
        print("[startup] Try 1) install cloudflared (no token), or 2) set NGROK_AUTHTOKEN for ngrok.")
        return None, None

    def _stop_public_tunnel(tunnel_obj) -> None:
        if tunnel_obj is None:
            return
        provider = str((tunnel_obj or {}).get("provider") or "").strip().lower()
        if provider == "ngrok":
            try:
                from pyngrok import ngrok

                tunnel = (tunnel_obj or {}).get("tunnel")
                public_url = str(getattr(tunnel, "public_url", "") or "").strip()
                if public_url:
                    ngrok.disconnect(public_url)
                ngrok.kill()
            except Exception:
                pass
            return
        if provider == "cloudflared":
            proc = (tunnel_obj or {}).get("process")
            if proc is None:
                return
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    host = str(os.getenv("HOST", "0.0.0.0") or "0.0.0.0").strip()
    port = int(os.getenv("PORT", "8000"))
    local_url = f"http://localhost:{port}/"
    lan_ip = _detect_lan_ip()
    lan_url = f"http://{lan_ip}:{port}/" if lan_ip else ""

    tunnel, public_url = _start_public_tunnel(port)

    print(f"[startup] Local URL:  {local_url}")
    if lan_url and lan_url != local_url:
        print(f"[startup] Local LAN:  {lan_url}")
    if public_url:
        print(f"[startup] Public URL: {public_url}")
    else:
        print("[startup] Public URL: unavailable (install cloudflared or configure ngrok token).")

    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        _stop_public_tunnel(tunnel)
