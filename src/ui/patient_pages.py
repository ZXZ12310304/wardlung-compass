import html
import json
import math


def render_patient_page(state: dict, ctx: dict) -> str:
    current_page = state.get("current_page", "dashboard")
    sidebar_data = ctx["get_patient_sidebar_data"](state)
    dashboard_data = ctx["get_patient_data"](state)
    completed = bool(dashboard_data.get("completed"))
    status_text = "Completed" if completed else "Not completed"
    status_class = "complete" if completed else "incomplete"
    ring_color = "#DDE4EE"
    ring_accent = "#CFE67E" if completed else "#6AB8C4"
    progress = 1.0 if completed else 0.25
    r = 26
    c = 2 * math.pi * r
    dash = c * progress
    gap = c - dash
    bullets = dashboard_data.get("bullets") or ["No care card published yet."]
    bullets_html = "".join(f"<li>{html.escape(str(b))}</li>" for b in bullets[:6])
    tts_lines = [str(b).strip() for b in bullets[:6] if str(b).strip()]
    tts_text = "Today's care card. " + " ".join(tts_lines) if tts_lines else ""
    tts_click_js = f"return wlSpeakText({json.dumps(tts_text, ensure_ascii=False)});"
    tts_icon = (
        "<svg class='icon' viewBox='0 0 24 24'>"
        "<path d='M11 5L6 9H3v6h3l5 4z'/>"
        "<path d='M15 9a5 5 0 0 1 0 6'/>"
        "<path d='M17.8 6.5a8.5 8.5 0 0 1 0 11'/>"
        "</svg>"
    )
    patient_id = sidebar_data.get("patient_id") or dashboard_data.get("patient_id")
    pref = ctx["get_prefs"](patient_id) if patient_id else {"font_size": "Normal"}
    font_size = state.get("settings_font") or pref.get("font_size", "Normal")
    main_class = "main font-large" if str(font_size).lower() == "large" else "main"
    current_page = state.get("current_page", "dashboard")
    toast_msg = state.get("toast", "")
    toast_html = f'<div class="toast show">{html.escape(toast_msg)}</div>' if toast_msg else ""
    nurse_detail = str(state.get("nurse_request_detail") or "").strip()
    nurse_image_name = str(state.get("nurse_request_image_name") or "").strip() or "Add image (optional)"
    nurse_audio_name = str(state.get("nurse_request_audio_name") or "").strip() or "Add audio (optional)"
    nurse_open_js = (
        "(function(){"
        "try{localStorage.setItem('wl_nurse_request_modal_open','1');}catch(e){}"
        "var m=document.getElementById('nurse_request_modal');"
        "if(m){m.style.display='flex';}"
        "})(); return false;"
    )
    nurse_close_js = (
        "(function(){"
        "try{localStorage.removeItem('wl_nurse_request_modal_open');}catch(e){}"
        "var m=document.getElementById('nurse_request_modal');"
        "if(m){m.style.display='none';}"
        "})(); return false;"
    )
    nurse_submit_js = (
        "(function(){"
        "var detailEl=document.getElementById('nurse_request_detail');"
        "var detail=detailEl?detailEl.value:'';"
        "if(!detail||!detail.trim()){wlShowToast('Please enter details before sending.'); if(detailEl){detailEl.focus();} return false;}"
        "var page=(window._wl_page||(function(){try{return localStorage.getItem('wl_page')||'';}catch(e){return '';}})());"
        "try{localStorage.removeItem('wl_nurse_request_modal_open');}catch(e){}"
        "wlApi('request_nurse_now', {reason:'Patient requested nurse assistance.', detail:detail, current_page:page});"
        "})(); return false;"
    )
    nurse_image_change_js = (
        "(function(el){"
        "var file=el.files&&el.files[0]; if(!file) return;"
        "var detailEl=document.getElementById('nurse_request_detail');"
        "var detail=detailEl?detailEl.value:'';"
        "var page=(window._wl_page||(function(){try{return localStorage.getItem('wl_page')||'';}catch(e){return '';}})());"
        "var fd=new FormData();"
        "fd.append('file', file);"
        "fd.append('detail', detail);"
        "fd.append('page', page);"
        "try{localStorage.setItem('wl_nurse_request_modal_open','1');}catch(e){}"
        "wlApiUpload('/api/request_nurse_image', fd);"
        "el.value='';"
        "})(this);"
    )
    nurse_audio_change_js = (
        "(function(el){"
        "var file=el.files&&el.files[0]; if(!file) return;"
        "var detailEl=document.getElementById('nurse_request_detail');"
        "var detail=detailEl?detailEl.value:'';"
        "var page=(window._wl_page||(function(){try{return localStorage.getItem('wl_page')||'';}catch(e){return '';}})());"
        "var fd=new FormData();"
        "fd.append('file', file);"
        "fd.append('detail', detail);"
        "fd.append('page', page);"
        "try{localStorage.setItem('wl_nurse_request_modal_open','1');}catch(e){}"
        "wlApiUpload('/api/request_nurse_audio', fd);"
        "el.value='';"
        "})(this);"
    )
    icons = ctx["icons"]
    nav_items = [
        (icons["dashboard"], "Dashboard", "nav_dashboard", "dashboard"),
        (icons["calendar"], "Daily Check", "nav_daily", "daily"),
        (icons["card"], "Care Cards", "nav_cards", "cards"),
        (icons["chat"], "Chat", "nav_chat", "chat"),
        (icons["inbox"], "Inbox", "nav_inbox", "inbox"),
        (icons["settings"], "Settings", "nav_settings", "settings"),
    ]
    def _nav_js(page_key: str) -> str:
        js = f"wlNav('{page_key}'); return false;"
        return html.escape(js, quote=True)

    nav_html = "".join(
        f"<div class=\"nav-item {'active' if current_page==key else ''}\" "
        f"data-page=\"{key}\" onclick=\"{_nav_js(key)}\">{icon}{label}</div>"
        for icon, label, btn, key in nav_items
    )
    cta_text = "View" if completed else "Complete now →"
    dashboard_html = f"""
    <div class="header-title">Dashboard</div>
    <div class="header-sub">Quick access to today's care</div>
    <div class="card-row">
      <div class="card">
        <h4>Today status</h4>
        <div class="status-pill {status_class}">{status_text}</div>
        <div class="link" onclick="{_nav_js('daily')}">Go to Daily Check</div>
      </div>
      <div class="card">
        <h4>Care Cards</h4>
        <div>Today: {dashboard_data.get('today_card_count',0)} cards</div>
        <div>{dashboard_data.get('unread_card_count',0)} unread / needs review</div>
      </div>
      <div class="card">
        <h4>Inbox</h4>
        <div>{dashboard_data.get('unread_msg_count',0)} unread</div>
        <div>Nurse: {html.escape(dashboard_data.get('latest_msg_preview') or 'No new messages')}</div>
      </div>
    </div>
    <div class="care-card" style="position:relative;">
      <div class="actions">
        <button class="icon-btn" title="Read aloud" aria-label="Read aloud" onclick="{html.escape(tts_click_js, quote=True)}">{tts_icon}</button>
      </div>
      <h3>Today's Care Card</h3>
      <ul>{bullets_html}</ul>
      <div class="view-all" onclick="{_nav_js('cards')}">View all Care Cards →</div>
    </div>
    <div class="quick-row">
      <div class="quick-card" onclick="{_nav_js('daily')}">
        <div class="q-title">Daily Check</div>
        <svg class="progress-ring" viewBox="0 0 60 60">
          <circle cx="30" cy="30" r="{r}" stroke="{ring_color}" stroke-width="8" fill="none" />
          <circle cx="30" cy="30" r="{r}" stroke="{ring_accent}" stroke-width="8" fill="none"
                  stroke-dasharray="{dash} {gap}" stroke-linecap="round" transform="rotate(-90 30 30)" />
        </svg>
        <div>{'Completed today' if completed else 'Not completed today'}</div>
        <div class="link" onclick="{_nav_js('daily')}">{cta_text}</div>
      </div>
      <div class="quick-card" onclick="{_nav_js('chat')}">
        <div class="q-title">Chat</div>
        <div class="qc-icon">{icons['mic']}</div>
        <div class="qc-text">Ask a question</div>
      </div>
      <div class="quick-card" onclick="{_nav_js('inbox')}">
        <div class="q-title">Inbox</div>
        <div class="qc-icon">{icons['mail']}</div>
        <div class="qc-text">{dashboard_data.get('unread_msg_count',0)} new messages</div>
      </div>
      <div class="quick-card" onclick="{_nav_js('settings')}">
        <div class="q-title">Settings</div>
        <div class="qc-icon">{icons['settings']}</div>
        <div class="qc-text">Change password</div>
      </div>
    </div>
    """
    daily_html = _render_daily_check_main(state, ctx)
    cards_html = _render_care_cards_main(state, ctx)
    chat_html = _render_chat_main(state, ctx)
    inbox_html = _render_inbox_main(state, ctx)
    settings_html = _render_settings_main(state, ctx)
    sections = [
        ("dashboard", dashboard_html),
        ("daily", daily_html),
        ("cards", cards_html),
        ("chat", chat_html),
        ("inbox", inbox_html),
        ("settings", settings_html),
    ]
    main_html = "".join(
        f"<div class=\"page-section\" data-page=\"{key}\" style=\"display:{'block' if key==current_page else 'none'};\">{content}</div>"
        for key, content in sections
    )
    nurse_modal_html = f"""
<div id="nurse_request_modal" class="care-modal-backdrop nurse-call-modal-backdrop" style="display:none" onclick="{html.escape(nurse_close_js, quote=True)}">
  <div class="care-modal nurse-call-modal" onclick="event.stopPropagation();">
    <div class="care-modal-scroll">
    <h3>Need Nurse Support</h3>
    <div class="care-modal-date">Please share key details so staff can triage quickly.</div>
    <div class="nurse-call-form">
      <textarea id="nurse_request_detail" class="nurse-call-textarea" rows="5" placeholder="Please describe what you need (required).">{html.escape(nurse_detail)}</textarea>
      <div class="nurse-call-attach-row">
        <button class="pill-btn" onclick="document.getElementById('nurse_request_audio_upload').click(); return false;">{html.escape(nurse_audio_name)}</button>
        <button class="pill-btn" onclick="document.getElementById('nurse_request_image_upload').click(); return false;">{html.escape(nurse_image_name)}</button>
        <input id="nurse_request_audio_upload" type="file" accept="audio/*" style="display:none" onchange="{html.escape(nurse_audio_change_js, quote=True)}" />
        <input id="nurse_request_image_upload" type="file" accept="image/*" style="display:none" onchange="{html.escape(nurse_image_change_js, quote=True)}" />
      </div>
      <div class="care-modal-actions">
        <button class="care-action care-action-primary" onclick="{html.escape(nurse_submit_js, quote=True)}">Send Request</button>
        <button class="care-action care-action-secondary" onclick="{html.escape(nurse_close_js, quote=True)}">Cancel</button>
      </div>
    </div>
    </div>
  </div>
</div>
<script>
(function(){{
  try {{
    if (localStorage.getItem('wl_nurse_request_modal_open') === '1') {{
      var modal = document.getElementById('nurse_request_modal');
      if (modal) {{
        modal.style.display = 'flex';
      }}
    }}
  }} catch (e) {{}}
}})();
</script>
"""

    html_out = f"""
<div class="dash-page">
  <div class="sidebar">
    <div class="brand">
      <img src="{ctx['logo_data']}" />
      <div class="brand-text">WardLung <span class="compass">Compass</span></div>
    </div>
    <div class="nav">{nav_html}</div>
    <div class="profile">
      <img src="{sidebar_data.get('avatar','')}" />
      <div>
        <div class="name">{html.escape(sidebar_data.get('display_name') or '')}</div>
        <div class="role">{html.escape(sidebar_data.get('role') or '')}</div>
      </div>
    </div>
    <div class="logout" onclick="{ctx['onclick']('do_logout')}">{icons['logout']} Log out</div>
  </div>
  <div class="{main_class}">
    {f'''
    <div id="nurse_call_fab_wrap" class="nurse-call-fab-wrap" style="{'display:none;' if current_page == 'chat' else ''}">
      <button class="nurse-call-fab" onclick="{html.escape(nurse_open_js, quote=True)}">Need Nurse</button>
    </div>
    '''}
    {main_html}
  </div>
</div>
{nurse_modal_html}
{toast_html}
"""
    return html_out


def _render_daily_check_main(state: dict, ctx: dict) -> str:
    state = ctx["init_daily_state"](state)
    step = int(state.get("daily_step", 1))
    answers = state.get("daily_answers") or ctx["default_daily_answers"]()
    pct = step * 20
    payload_attr = html.escape(json.dumps(answers, ensure_ascii=False))

    def _radio_option(name, value):
        checked = "checked" if answers.get(name) == value else ""
        return f"""
<label class="dc-radio">
  <input type="radio" name="{name}" value="{value}" {checked} />
  <span class="dc-radio-pill">
    <span class="dc-radio-icon">&#10003;</span>
    <span>{html.escape(value)}</span>
  </span>
</label>
"""

    def _chip_option(value):
        selected = value in (answers.get("diet_triggers") or [])
        checked = "checked" if selected else ""
        return f"""
<label class="dc-chip">
  <input type="checkbox" name="diet_triggers" value="{value}" {checked} />
  <span class="dc-chip-pill">
    <span class="dc-chip-check">&#10003;</span>
    <span>{html.escape(value)}</span>
  </span>
</label>
"""

    def _pill_group(name, current):
        options = ["None", "Mild", "Moderate", "Severe"]
        pills = []
        for opt in options:
            checked = "checked" if current == opt else ""
            pills.append(
                f"""<label class="dc-pill"><input type="radio" name="{name}" value="{opt}" {checked} /><span>{opt}</span></label>"""
            )
        return "".join(pills)

    def _section_header(title):
        return f"<div class=\"dc-section-title\">{html.escape(title)}</div>"

    if step == 1:
        content = "".join([
            _section_header("Diet today"),
            "".join(
                _radio_option("diet_status", v)
                for v in ["Normal", "Reduced appetite", "Nausea", "Can't eat"]
            ),
            _section_header("Dietary triggers (multiple choice)"),
            "<div class=\"dc-chips\">"
            + "".join(
                _chip_option(v)
                for v in ["Spicy", "High sugar", "Oily", "Cold drinks", "Dairy", "Caffeine/Tea"]
            )
            + "</div>",
        ])
    elif step == 2:
        hours_val = html.escape(str(answers.get("sleep_hours", "")))
        content = "".join([
            _section_header("Sleep"),
            "".join(_radio_option("sleep_quality", v) for v in ["Good", "Fair", "Poor"]),
            "<div class=\"dc-slider\"><label>Hours slept (0–12)</label>"
            f"<input id=\"sleep_hours\" type=\"range\" min=\"0\" max=\"12\" value=\"{hours_val or '0'}\" />"
            "</div>",
        ])
    elif step == 3:
        content = "".join([
            _section_header("Medication adherence"),
            "".join(_radio_option("med_adherence", v) for v in ["Took on time", "Missed", "Not sure"]),
        ])
    elif step == 4:
        symptoms = answers.get("symptoms") or {}
        content = "".join([
            _section_header("Symptoms"),
            f"<div class=\"dc-symptom\"><div class=\"label\">Cough</div><div class=\"dc-pills\">{_pill_group('symptom_cough', symptoms.get('cough',''))}</div></div>",
            f"<div class=\"dc-symptom\"><div class=\"label\">Shortness of breath</div><div class=\"dc-pills\">{_pill_group('symptom_sob', symptoms.get('sob',''))}</div></div>",
            f"<div class=\"dc-symptom\"><div class=\"label\">Chest pain</div><div class=\"dc-pills\">{_pill_group('symptom_chest_pain', symptoms.get('chest_pain',''))}</div></div>",
        ])
    else:
        notes = html.escape(str(answers.get("notes_text", "")))
        content = "".join([
            _section_header("Notes"),
            f"<textarea id=\"dc_notes\" class=\"dc-textarea\" placeholder=\"Describe anything else...\">{notes}</textarea>",
        ])

    next_label = "Submit and generate care card" if step == 5 else "Next"
    next_action = "dc_submit" if step == 5 else "dc_next"
    prev_disabled = "disabled" if step == 1 else ""

    return f"""
<div class="daily-page">
  <div>
    <div class="daily-header-title">Daily Check</div>
    <div class="daily-header-sub">Tell us how you feel today</div>
  </div>
  <div class="daily-check-card" data-answers='{payload_attr}'>
    <div class="daily-progress">
      <div>Step {step} of 5</div>
      <div class="progress-bar"><span style="width:{pct}%"></span></div>
      <div class="progress-pct">{pct}%</div>
    </div>
    <div class="dc-content">{content}</div>
    <div class="dc-actions">
      <button class="dc-btn" {prev_disabled} onclick="{ctx['dc_onclick']('dc_prev')}">Previous</button>
      <div class="dc-save" onclick="{ctx['dc_onclick']('dc_save')}">Save draft</div>
      <button class="dc-btn dc-next" onclick="{ctx['dc_onclick'](next_action)}">{next_label}</button>
    </div>
  </div>
</div>
"""


def _render_care_cards_main(state: dict, ctx: dict) -> str:
    patient_id = state.get("patient_id") or ctx["get_any_patient_id"]()
    search = state.get("care_search", "")
    cards = ctx["load_care_cards"](patient_id, search=search)
    highlight_id = state.get("highlight_card_id")
    modal_id = state.get("care_modal_id")
    search_send_js = (
        "(function(){var el=document.querySelector('#care_search_input');"
        "var val=el?el.value:'';"
        "wlApi('care_search', {q: val});})();"
    )
    search_keydown_js = f"if(event.key==='Enter'){{{search_send_js}}}"
    grid_html = ""
    if not cards:
        grid_html = "<div class='card'>No care cards yet. Complete Daily Check to generate today's card.</div>"
    else:
        for c in cards:
            bullets = c.get("bullets") or []
            b1 = bullets[0] if len(bullets) > 0 else ""
            b2 = bullets[1] if len(bullets) > 1 else ""
            status_html = "<span class='care-status-dot'></span>" if not c.get("understood") else "<span class='care-status-check'>&#10003;</span>"
            active_cls = " style='outline:2px solid #6AB8C4;'" if highlight_id == c["card_id"] else ""
            grid_html += f"""
<div class='care-card-item'{active_cls} onclick="{ctx['ui_onclick']('care_open', {'card_id': c['card_id']})}">
  <div class='care-pill'>Daily</div>
  <div class='care-title'>{html.escape(c.get('title',''))}</div>
  <ul class='care-bullets'>
    <li>{html.escape(b1)}</li>
    <li>{html.escape(b2)}</li>
  </ul>
  <div class='care-date-row'><span>{html.escape(c.get('date',''))}</span>{status_html}</div>
</div>
"""
    modal_html = ""
    if modal_id:
        card = next((x for x in cards if x["card_id"] == modal_id), None)
        if card is None:
            card = {"title": "Care Card", "date": "", "bullets": []}
        title_text = str(card.get("title") or "Care Card").strip()
        date_text = str(card.get("date") or "").strip()
        focus_text = str(card.get("one_liner") or "").strip()
        do_items = [str(b).strip() for b in (card.get("bullets") or []) if str(b).strip()]
        dont_items = [str(b).strip() for b in (card.get("follow_up") or []) if str(b).strip()]
        help_items = [str(b).strip() for b in (card.get("red_flags") or []) if str(b).strip()]
        do_list = "".join(f"<li>{html.escape(b)}</li>" for b in do_items)
        dont_list = "".join(f"<li>{html.escape(b)}</li>" for b in dont_items)
        help_list = "".join(f"<li>{html.escape(b)}</li>" for b in help_items)
        focus_html = f"<div class='care-focus'>{html.escape(focus_text)}</div>" if focus_text else ""
        modal_tts_parts = [title_text]
        if focus_text:
            modal_tts_parts.append(focus_text)
        if do_items:
            modal_tts_parts.append("Do.")
            modal_tts_parts.extend(do_items)
        if dont_items:
            modal_tts_parts.append("Don't.")
            modal_tts_parts.extend(dont_items)
        if help_items:
            modal_tts_parts.append("Get help now.")
            modal_tts_parts.extend(help_items)
        modal_tts_text = " ".join(x for x in modal_tts_parts if x).strip()
        modal_tts_js = f"return wlSpeakText({json.dumps(modal_tts_text, ensure_ascii=False)});"
        modal_tts_icon = (
            "<svg class='icon' viewBox='0 0 24 24'>"
            "<path d='M11 5L6 9H3v6h3l5 4z'/>"
            "<path d='M15 9a5 5 0 0 1 0 6'/>"
            "<path d='M17.8 6.5a8.5 8.5 0 0 1 0 11'/>"
            "</svg>"
        )
        modal_html = f"""
<div class='care-modal-backdrop' onclick="{ctx['ui_onclick']('care_close')}">
<div class='care-modal' onclick="event.stopPropagation();">
  <div class='care-modal-scroll'>
  <div class='care-modal-head'>
    <div>
      <h3>{html.escape(title_text)}</h3>
      <div class='care-modal-date'>{html.escape(date_text)}</div>
    </div>
    <button class='care-modal-tts' title='Read aloud' aria-label='Read aloud' onclick="{html.escape(modal_tts_js, quote=True)}">{modal_tts_icon}</button>
  </div>
  {focus_html}
  <div class='care-section'>
    <div class='care-section-title'>DO</div>
    <ul>{do_list}</ul>
  </div>
  <div class='care-section'>
    <div class='care-section-title'>DON'T</div>
    <ul>{dont_list}</ul>
  </div>
  <div class='care-section'>
    <div class='care-section-title'>GET HELP NOW</div>
    <ul>{help_list}</ul>
  </div>
  <div class='care-modal-actions'>
    <button class='care-action care-action-primary' onclick="{ctx['ui_onclick']('care_mark', {'card_id': card.get('card_id','')})}">Mark as understood</button>
    <button class='care-action care-action-delete' onclick="if(!confirm('Delete this care card?')) return false; {ctx['ui_onclick']('care_delete', {'card_id': card.get('card_id','')})}">Delete</button>
    <button class='care-action care-action-secondary' onclick="{ctx['ui_onclick']('care_close')}">Close</button>
  </div>
  </div>
</div>
</div>
"""
    icons = ctx["icons"]
    return f"""
<div>
  <div class='care-header'>
    <div>
      <h1>Care Cards</h1>
      <div class='care-sub'>Your daily care cards</div>
    </div>
  </div>
  <div class='care-topbar'>
    <div class='care-search'>
      {icons['search'] if 'search' in icons else ''}
      <input id='care_search_input' type='text' placeholder='Search cards' value='{html.escape(search)}'
        onkeydown="{html.escape(search_keydown_js, quote=True)}" />
    </div>
    <button class='care-search-btn' onclick="{html.escape(search_send_js, quote=True)}">Search</button>
    <div class='care-sort'>Sorted by date (newest first)</div>
  </div>
  <div class='care-grid'>
    {grid_html}
  </div>
</div>
{modal_html}
"""


def _render_chat_main(state: dict, ctx: dict) -> str:
    history = state.get("chat_history") or []
    pending = bool(state.get("chat_pending"))
    chat_tts_icon = (
        "<svg class='icon' viewBox='0 0 24 24'>"
        "<path d='M11 5L6 9H3v6h3l5 4z'/>"
        "<path d='M15 9a5 5 0 0 1 0 6'/>"
        "<path d='M17.8 6.5a8.5 8.5 0 0 1 0 11'/>"
        "</svg>"
    )
    bubble_items = []
    for m in history[-8:]:
        role = str(m.get("role") or "")
        text = str(m.get("text") or "")
        if role == "assistant":
            speak_js = f"return wlSpeakText({json.dumps(text, ensure_ascii=False)});"
            bubble_items.append(
                "<div class='bubble assistant bubble-with-tts'>"
                f"<button class='chat-tts-btn' title='Read aloud' aria-label='Read aloud' onclick=\"{html.escape(speak_js, quote=True)}\">{chat_tts_icon}</button>"
                f"<div class='bubble-text'>{html.escape(text)}</div>"
                "</div>"
            )
        else:
            bubble_items.append(f"<div class='bubble user'><div class='bubble-text'>{html.escape(text)}</div></div>")
    bubbles = "".join(bubble_items)
    thinking_style = "display:block;" if pending else "display:none;"
    thinking_html = f"""
<div id="chat_thinking" class="bubble assistant chat-thinking" style="{thinking_style}">
  <div class="thinking-title">Thinking...</div>
  <div class="thinking-steps">
    <div class="thinking-step step1">Understanding your question</div>
    <div class="thinking-step step2">Checking patient context</div>
    <div class="thinking-step step3">Retrieving knowledge</div>
    <div class="thinking-step step4">Drafting response</div>
  </div>
  <div class="thinking-bar"><span></span></div>
</div>
"""
    empty_html = ""
    if not history:
        def _chat_suggest_btn(text: str) -> str:
            msg = json.dumps(text)
            js = (
                "var root=document.querySelector('gradio-app');"
                "var dom=root&&root.shadowRoot?root.shadowRoot:document;"
                "var el=dom.querySelector('#chat_input');"
                f"if(el){{el.value={msg}; el.focus();}}"
            )
            return f"<button onclick=\"{html.escape(js, quote=True)}\">{html.escape(text)}</button>"
        empty_html = f"""
<div class='chat-empty'>
  <div class='chat-empty-title'>How can I help today?</div>
  <div class='chat-empty-sub'>Try one of these:</div>
  <div class='chat-suggestions'>
    {_chat_suggest_btn('What should I watch for today?')}
    {_chat_suggest_btn('Explain pneumonia in simple terms.')}
    {_chat_suggest_btn('What are warning signs I should call the nurse for?')}
    {_chat_suggest_btn('How can I rest and recover today?')}
  </div>
</div>
"""
    bubbles = thinking_html + (bubbles if history else "")
    send_js = "(function(){var inputEl=document.querySelector('#chat_input');if(!inputEl) return;var msg=inputEl.value; if(!msg) return; var thinking=document.querySelector('#chat_thinking'); if(thinking){thinking.style.display='block';} var page=(window._wl_page||(function(){try{return localStorage.getItem('wl_page')||'';}catch(e){return '';}})()); wlApi('chat_send', {message: msg, current_page: page}); inputEl.value='';})();"

    mic_down_js = "(function(){var btn=document.querySelector('#chat_mic_btn');if(btn) btn.classList.add('recording');if(window._wl_rec && window._wl_rec.state==='recording') return; if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){return;} navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){window._wl_stream=stream;var rec=new MediaRecorder(stream);window._wl_rec=rec;window._wl_chunks=[];rec.ondataavailable=function(e){if(e.data&&e.data.size>0) window._wl_chunks.push(e.data);};rec.onstop=function(){var blob=new Blob(window._wl_chunks,{type:rec.mimeType||'audio/webm'});var inputEl=document.querySelector('#chat_input');var msg=inputEl?inputEl.value:'';var page=(window._wl_page||(function(){try{return localStorage.getItem('wl_page')||'';}catch(e){return '';}})()); var fd=new FormData(); fd.append('file', blob, 'voice.webm'); fd.append('message', msg); fd.append('page', page); var thinking=document.querySelector('#chat_thinking'); if(thinking){thinking.style.display='block';} wlApiUpload('/api/chat_voice', fd); if(inputEl) inputEl.value=''; if(window._wl_stream){window._wl_stream.getTracks().forEach(function(t){t.stop();});}};rec.start();}).catch(function(){});})();"
    mic_up_js = "(function(){var btn=document.querySelector('#chat_mic_btn');if(btn) btn.classList.remove('recording');var rec=window._wl_rec; if(rec && rec.state==='recording'){rec.stop();}})();"
    image_js = "(function(){var inp=document.querySelector('#chat_image_upload'); if(inp) inp.click();})();"
    daily = state.get("daily_answers") or {}
    summary = f"Today's daily check: {daily.get('diet_status','')} {daily.get('sleep_quality','')}"
    icons = ctx["icons"]
    image_change_js = (
        "(function(el){var file=el.files&&el.files[0]; if(!file) return; "
        "var input=document.querySelector('#chat_input'); var msg=input?input.value:'';"
        "var page=(window._wl_page||(function(){try{return localStorage.getItem('wl_page')||'';}catch(e){return '';}})());"
        "var fd=new FormData(); fd.append('file', file); fd.append('message', msg); fd.append('page', page);"
        "var thinking=document.querySelector('#chat_thinking'); if(thinking){thinking.style.display='block';}"
        "wlApiUpload('/api/chat_image', fd); el.value=''; if(input) input.value='';})(this);"
    )
    return f"""
<div class='chat-layout'>
  <div class='chat-panel'>
    <div class='chat-title'>{icons['chat']} Chat with WardLung</div>
    <div class='chat-bubbles'>{empty_html}{bubbles}</div>
    <div class='chat-input-bar'>
      <input id='chat_input' type='text' placeholder='Type a message...' />
      <button id='chat_mic_btn' class='chat-btn' onmousedown="{html.escape(mic_down_js, quote=True)}"
              onmouseup="{html.escape(mic_up_js, quote=True)}" onmouseleave="{html.escape(mic_up_js, quote=True)}">Voice</button>
      <button class='chat-btn' onclick="{html.escape(image_js, quote=True)}">Image</button>
      <input id='chat_image_upload' type='file' accept='image/*' style='display:none' onchange="{html.escape(image_change_js, quote=True)}" />
      <button class='chat-send' onclick="{send_js}">Send</button>
    </div>
    <div class='chat-note'>No medication dosage advice. Ask your nurse for urgent issues.</div>
  </div>
  <div class='safety-panel'>
    <div class='safety-card'>
      <h4>What I can / can't do</h4>
      <div><b>Can:</b><ul><li>Explain terms</li><li>Summarize info</li><li>Prepare questions for clinicians</li></ul></div>
      <div><b>Can't:</b><ul><li>Provide medication dosage</li><li>Offer definitive diagnosis</li><li>Perform urgent triage</li></ul></div>
    </div>
    <div class='safety-card'>
      <h4>When to call nurse</h4>
      <ul><li>Severe shortness of breath</li><li>New or worsening chest pain</li><li>Sudden confusion</li></ul>
    </div>
    <div class='safety-card'>
      <h4>Recent context</h4>
      <div>{html.escape(summary)}</div>
      <div class='link' onclick="{ctx['ui_onclick']('care_open_latest')}">Open latest Care Card</div>
    </div>
  </div>
</div>
"""


def _render_inbox_main(state: dict, ctx: dict) -> str:
    patient_id = state.get("patient_id") or ctx["get_any_patient_id"]()
    category = state.get("inbox_filter", "All")
    search = state.get("inbox_search", "")
    messages = ctx["load_inbox_messages"](patient_id, category=category, search=search)
    selected_id = state.get("inbox_selected_id") or (messages[0]["message_id"] if messages else None)
    tabs = ["All", "Nurse", "Doctor", "System"]
    tab_html = ""
    for t in tabs:
        active = "active" if t == category else ""
        tab_html += f"<div class=\"inbox-tab {active}\" onclick=\"{ctx['ui_onclick']('inbox_filter', {'category': t})}\">{t}</div>"
    search_send_js = (
        "(function(){var el=document.querySelector('#inbox_search_input');"
        "var val=el?el.value:'';"
        "wlApi('inbox_search', {q: val});})();"
    )
    search_keydown_js = f"if(event.key==='Enter'){{{search_send_js}}}"
    list_html = ""
    if not messages:
        list_html = "<div class='msg-empty'>No messages yet.</div>"
    else:
        for m in messages:
            dot = "<span class='msg-dot'></span>" if m.get("unread") else ""
            active = "active" if m.get("message_id") == selected_id else ""
            list_html += f"""
<div class='msg-item {active}' onclick="{ctx['ui_onclick']('inbox_select', {'message_id': m['message_id']})}">
  <div class='title'>{html.escape(m['sender_name'])}</div>
  <div>{html.escape(m['subject'])}</div>
  <div class='meta'>{dot} {html.escape(m['created_at'][:10])}</div>
</div>
"""
    detail_html = ""
    if selected_id and messages:
        selected = next((x for x in messages if x["message_id"] == selected_id), messages[0])
        body_html = html.escape(selected["body"]).replace("\n", "<br/>")
        detail_html = f"""
<div class='detail-title'>{html.escape(selected['subject'])}</div>
<div class='detail-meta'>From: {html.escape(selected['sender_name'])} | Date: {html.escape(selected['created_at'][:16])}</div>
<div class='detail-body'>{body_html}</div>
<div class='detail-actions'>
  <button class='settings-save' onclick="{ctx['ui_onclick']('inbox_ack', {'message_id': selected['message_id']})}">Acknowledge</button>
  <button class='dc-btn' onclick="{ctx['ui_onclick']('inbox_reply', {'message_id': selected['message_id']})}">Reply</button>
</div>
<div class='detail-actions detail-actions-end'>
  <button class='delete-btn-themed' onclick="if(!confirm('Delete this message?')) return false; {ctx['ui_onclick']('inbox_delete', {'message_id': selected['message_id']})}">Delete</button>
</div>
"""
    icons = ctx["icons"]
    return f"""
<div class='inbox-page'>
  <div class='header-title'>Inbox</div>
  <div class='header-sub'>Messages and updates from your care team</div>
  <div class='inbox-layout'>
    <div class='inbox-list'>
      <div class='inbox-tabs'>{tab_html}</div>
      <div class='inbox-search'>
        {icons['search'] if 'search' in icons else ''}
        <input id='inbox_search_input' type='text' placeholder='Search messages' value='{html.escape(search)}'
          onkeydown="{html.escape(search_keydown_js, quote=True)}" />
      </div>
      <button class='inbox-search-btn' onclick="{html.escape(search_send_js, quote=True)}">Search</button>
      <div class='msg-list'>{list_html}</div>
    </div>
    <div class='inbox-detail'>
      {detail_html if detail_html else "<div class='msg-empty'>Select a message</div>"}
    </div>
  </div>
</div>
"""


def _render_settings_main(state: dict, ctx: dict) -> str:
    patient_id = state.get("patient_id") or ctx["get_any_patient_id"]()
    prefs = ctx["get_prefs"](patient_id)
    font = state.get("settings_font") or prefs.get("font_size", "Normal")
    display_name = prefs.get("display_name") or patient_id
    avatar_src = prefs.get("avatar_data") or ctx["avatar_data_uri"](display_name)
    save_js = (
        "(function(){"
        "var root=document.querySelector('gradio-app');var dom=root&&root.shadowRoot?root.shadowRoot:document;"
        "var name=dom.querySelector('#display_name');"
        "var file=dom.querySelector('#avatar_file');"
        "var payload={display_name: name?name.value:'', font_size: '%s'};"
        "var send=function(p){var input=dom.querySelector('#ui_payload textarea, #ui_payload input');"
        "if(input){input.value=JSON.stringify(p); input.dispatchEvent(new Event('input',{bubbles:true}));}"
        "var btn=dom.querySelector('#settings_save'); if(btn) btn.click();};"
        "if(file && file.files && file.files[0]){"
        "var reader=new FileReader();"
        "reader.onload=function(e){payload.avatar_data=e.target.result||''; send(payload);};"
        "reader.readAsDataURL(file.files[0]);"
        "} else { send(payload); }"
        "})();"
    ) % font
    pass_js = "(function(){var root=document.querySelector('gradio-app');var dom=root&&root.shadowRoot?root.shadowRoot:document;var oldp=dom.querySelector('#old_pass');var newp=dom.querySelector('#new_pass');var conf=dom.querySelector('#confirm_pass');var payload={old: oldp?oldp.value:'', new: newp?newp.value:'', confirm: conf?conf.value:''};var input=dom.querySelector('#ui_payload textarea, #ui_payload input'); if(input){input.value=JSON.stringify(payload); input.dispatchEvent(new Event('input',{bubbles:true}));} var btn=dom.querySelector('#settings_pass'); if(btn) btn.click();})();"
    avatar_onchange_js = (
        "var file=this.files&&this.files[0];"
        "if(!file) return;"
        "var root=document.querySelector('gradio-app');"
        "var dom=root&&root.shadowRoot?root.shadowRoot:document;"
        "var reader=new FileReader();"
        "reader.onload=function(e){var img=dom.querySelector('#avatar_preview_img'); if(img){img.src=e.target.result;} var nav=dom.querySelector('.profile img'); if(nav){nav.src=e.target.result;}};"
        "reader.readAsDataURL(file);"
    )
    return f"""
<div>
  <div class='header-title'>Settings</div>
  <div class='header-sub'>Manage your account and preferences</div>
  <div class='settings-grid'>
    <div class='settings-card'>
      <h4>Account</h4>
      <div class='settings-field'>
        <label>Patient ID</label>
        <input type='text' value='{html.escape(patient_id)}' readonly />
      </div>
      <div class='settings-field'>
        <label>Display name</label>
        <input id='display_name' type='text' value='{html.escape(display_name)}' placeholder='Your name' />
      </div>
      <div class='settings-field'>
        <label>Avatar</label>
        <div class='avatar-upload'>
          <div class='avatar-preview'><img id='avatar_preview_img' src='{avatar_src}' /></div>
          <div class='avatar-input'>
            <label class='upload-btn' for='avatar_file'>Upload avatar</label>
            <input id='avatar_file' class='avatar-file' type='file' accept='image/*'
              onchange="{html.escape(avatar_onchange_js, quote=True)}" />
          </div>
        </div>
      </div>
      <div class='settings-field'>
        <label>Font size</label>
        <div class='settings-toggle'>
          <button class='{'active' if font=='Normal' else ''}' onclick="{ctx['ui_onclick']('settings_font', {'font_size':'Normal'})}">Normal</button>
          <button class='{'active' if font=='Large' else ''}' onclick="{ctx['ui_onclick']('settings_font', {'font_size':'Large'})}">Large</button>
        </div>
      </div>
      <div class='settings-field'>
        <button class='settings-save' onclick="{save_js}">Save</button>
      </div>
    </div>
    <div class='settings-card'>
      <h4>Security</h4>
      <div class='settings-field'>
        <label>Old password</label>
        <input id='old_pass' type='password' placeholder='Old password' />
      </div>
      <div class='settings-field'>
        <label>New password</label>
        <input id='new_pass' type='password' placeholder='New password' />
      </div>
      <div class='settings-field'>
        <label>Confirm password</label>
        <input id='confirm_pass' type='password' placeholder='Confirm password' />
      </div>
      <div class='settings-field'>
        <button class='settings-save' onclick="{pass_js}">Save password</button>
      </div>
    </div>
  </div>
</div>
"""
