import ast
import html
import json
import re


def _action_js(action: str, payload: dict | None = None) -> str:
    payload = payload or {}
    payload_json = json.dumps(payload, ensure_ascii=False)
    js = (
        "(function(){"
        "var page=(window._wl_page||(function(){try{return localStorage.getItem('wl_page')||'';}catch(e){return '';}})());"
        f"var p={payload_json}; p.current_page=page; wlApi('{action}', p);"
        "})(); return false;"
    )
    return html.escape(js, quote=True)


def _to_english_gap(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return "Missing clinical input detected."
    fixed = {
        "病史缺少既往肺病/免疫抑制/近期抗生素等关键信息。": "Important history is incomplete (lung disease, immunosuppression, recent antibiotics).",
        "缺少血氧（SpO2），建议补充或测量。": "No recent SpO2 record.",
        "缺少体温信息，建议补充或测量。": "No recent temperature record.",
        "缺少呼吸频率，建议补充。": "No recent respiratory-rate record.",
        "缺少心率，建议补充。": "No recent heart-rate record.",
        "鉴别诊断生成失败。": "Differential diagnosis generation failed.",
    }
    if text in fixed:
        return fixed[text]
    if re.search(r"[\u4e00-\u9fff]", text):
        return "Missing or inconsistent clinical input detected. Please update nursing records."
    return text


def _clinical_summary_from_diag(diag: dict) -> str:
    info = diag if isinstance(diag, dict) else {}
    primary = str(info.get("primary_diagnosis") or "").strip()
    risk = str(info.get("risk_level") or "").strip()
    drivers = info.get("risk_drivers") if isinstance(info.get("risk_drivers"), list) else []
    summary = str(info.get("gentle_summary") or "").strip() or str(info.get("summary") or "").strip()
    if summary:
        return summary
    if primary:
        text = f"Current status suggests {primary}"
        if risk:
            text += f" with {risk.lower()} risk"
        text += "."
        if drivers:
            top_drivers = ", ".join(str(x) for x in drivers[:2] if str(x).strip())
            if top_drivers:
                text += f" Main factors: {top_drivers}."
        return text
    if risk:
        return f"Current status risk level is {risk}."
    return "Assessment generated. Review details for current patient status."


def _nav_item(icon: str, label: str, page: str, current: str) -> str:
    active = "active" if page == current else ""
    return (
        f"<div class='nav-item {active}' data-page='{page}' onclick=\"wlNav('{page}'); return false;\">"
        f"{icon}{html.escape(label)}</div>"
    )


def _render_sidebar(state: dict, ctx: dict, current_page: str) -> str:
    icons = ctx.get("icons", {})
    sidebar_data = ctx["get_nurse_sidebar_data"](state)
    nav_items = [
        (icons.get("dashboard", ""), "Nurse Dashboard", "ward_dashboard"),
        (icons.get("calendar", ""), "Vitals & MAR", "vitals_mar"),
        (icons.get("card", ""), "Generate Assessment", "generate_assessment"),
        (icons.get("inbox", ""), "Handover Summary", "handover_summary"),
        (icons.get("chat", ""), "Nurse Inbox", "nurse_inbox"),
        (icons.get("settings", ""), "Settings", "settings"),
    ]
    nav_html = "".join(_nav_item(icon, label, page, current_page) for icon, label, page in nav_items)
    logout_builder = ctx.get("onclick")
    logout_onclick = logout_builder("do_logout") if callable(logout_builder) else ""
    return f"""
  <div class="sidebar">
    <div class="brand">
      <img src="{ctx.get('logo_data','')}" />
      <div class="brand-text">WardLung <span class="compass">Compass</span></div>
    </div>
    <div class="nav">{nav_html}</div>
    <div class="profile">
      <img src="{sidebar_data.get('avatar','')}" />
      <div>
        <div class="name">{html.escape(sidebar_data.get('display_name') or sidebar_data.get('staff_id','Staff'))}</div>
        <div class="role">{html.escape(sidebar_data.get('role','Nurse'))}</div>
      </div>
    </div>
    <div class="logout" onclick="{logout_onclick}">{icons.get('logout','')} Log out</div>
  </div>
"""


def _render_toolbar(data: dict) -> str:
    ward_label = data.get("ward_label", "Ward A")
    shift = data.get("shift", "Morning")
    search = data.get("search", "")
    filter_tag = data.get("filter", "All")
    search_send_js = _action_js("ward_update", {"search": "__VALUE__"}).replace("__VALUE__", "'+val+'")
    filter_buttons = ""
    for t in ["All", "Stable", "Needs Attention"]:
        active = "active" if t == filter_tag else ""
        filter_buttons += f"<button class='chip {active}' onclick=\"{_action_js('ward_update', {'filter': t})}\">{t}</button>"
    return f"""
  <div class='staff-toolbar'>
    <div class='toolbar-item toolbar-fixed'>{html.escape(ward_label)}</div>
    <div class='toolbar-item toolbar-fixed'>Auto shift: {html.escape(shift)}</div>
    <div class='toolbar-search'>
      <input type='text' placeholder='Search Bed / Patient ID' value='{html.escape(search)}'
        onkeydown="if(event.key==='Enter'){{var val=this.value; {search_send_js}}}" />
    </div>
    <div class='toolbar-filters'>{filter_buttons}</div>
    <button class='toolbar-refresh' onclick="{_action_js('ward_update', {'refresh': True})}">Refresh</button>
  </div>
"""


def _page_header(title: str, subtitle: str) -> str:
    return (
        f"<div class='header-title'>{html.escape(title)}</div>"
        f"<div class='header-sub'>{html.escape(subtitle)}</div>"
    )


def _render_ward_dashboard(state: dict, ctx: dict) -> str:
    data = ctx["get_dashboard_data"](state)
    rows = ""
    for p in data.get("patients", []):
        risk_class = f"risk-{p.get('risk_level','stable')}"
        rows += f"""
<div class='ward-row'>
  <div>{html.escape(str(p.get('bed_id','--')))}</div>
  <div class='mono'>{html.escape(str(p.get('patient_id','')))}</div>
  <div><span class='risk-badge {risk_class}'>{html.escape(p.get('risk_label','Stable'))}</span></div>
  <div>{html.escape(str(p.get('last_vitals','--')))}</div>
  <div>{html.escape(str(p.get('last_mar','--')))}</div>
  <div>{html.escape(str(p.get('last_assessment','Pending')))}</div>
  <div><button class='pill-btn' onclick="{_action_js('nurse_select_patient', {'patient_id': p.get('patient_id'), 'bed_id': p.get('bed_id')})}">Select</button></div>
</div>
"""
    if not rows:
        rows = "<div class='ward-row empty'>No patients found.</div>"

    pending_html = ""
    for r in data.get("pending_requests", []):
        tags = "".join(f"<span class='tag tag-teal'>{html.escape(t)}</span>" for t in r.get("tags", [])[:2])
        pending_html += f"""
<div class='pending-item'>
  <div class='pending-time'>{html.escape(str(r.get('created_at',''))[:16])}</div>
  <div class='pending-summary'>{html.escape(r.get('summary',''))}</div>
  <div class='pending-tags'>{tags}</div>
  <button class='pill-btn' onclick="{_action_js('requests_select', {'request_id': r.get('request_id')})}">Open</button>
</div>
"""
    if not pending_html:
        pending_html = "<div class='empty'>No pending requests.</div>"

    tasks_html = ""
    for t in data.get("tasks", []):
        checked = "checked" if t.get("done") else ""
        tasks_html += f"""
<label class='task-item'>
  <input type='checkbox' {checked} onclick="{_action_js('task_toggle', {'task_id': t.get('task_id')})}" />
  <span>{html.escape(t.get('label',''))}</span>
</label>
"""

    return f"""
<div class='ward-dashboard-page'>
  {_page_header('Nurse Dashboard', 'Monitor bed status, risk signals, and shift priorities.')}
  {_render_toolbar(data)}
  <div class='ward-grid'>
    <div class='ward-table card'>
      <div class='card-title'>Patient List</div>
      <div class='ward-head'>
        <div>Bed</div><div>Patient ID</div><div>Risk Status</div><div>Last Vitals</div>
        <div>Last MAR</div><div>Last Assessment</div><div>Action</div>
      </div>
      {rows}
    </div>
    <div class='ward-side'>
      <div class='card pending-card'>
        <div class='card-title'>Pending Requests</div>
        <div class='pending-list'>{pending_html}</div>
      </div>
      <div class='card tasks-card'>
        <div class='card-title'>Today Tasks</div>
        <div class='tasks-list'>{tasks_html}</div>
      </div>
    </div>
  </div>
</div>
"""


def _render_vitals_mar(state: dict, ctx: dict) -> str:
    data = ctx["get_vitals_data"](state)
    picker = data.get("picker", {})
    options_html = "".join(
        f"<option value='{o['value']}' data-bed='{html.escape(o.get('bed_id') or '')}' {'selected' if o['value']==picker.get('selected') else ''}>{html.escape(o['label'])}</option>"
        for o in picker.get("options", [])
    )
    patient = data.get("patient") or {}
    vitals = data.get("vitals") or {}
    mar_items = data.get("mar_items") or []
    alerts = data.get("alerts") or []
    recent = data.get("recent") or []
    selected_patient_json = json.dumps(picker.get("selected"))
    vitals_save_js = (
        "(function(){"
        "var t=document.getElementById('v_temp');"
        "var hr=document.getElementById('v_hr');"
        "var rr=document.getElementById('v_rr');"
        "var bp=document.getElementById('v_bp');"
        "var spo=document.getElementById('v_spo2');"
        "var pn=document.getElementById('v_pain');"
        "var vitals={"
        "temperature_c:(t&&t.value)||'',"
        "heart_rate:(hr&&hr.value)||'',"
        "resp_rate:(rr&&rr.value)||'',"
        "bp:(bp&&bp.value)||'',"
        "spo2:(spo&&spo.value)||'',"
        "pain:(pn&&pn.value)||''"
        "};"
        f"wlApi('vitals_save', {{patient_id:{selected_patient_json}, vitals:vitals, current_page:'vitals_mar'}});"
        "})();"
    )
    mar_save_js = (
        "(function(){"
        "var rows=[];"
        "document.querySelectorAll('.mar-row').forEach(function(r){"
        "var name=r.getAttribute('data-name');"
        "var dose=r.getAttribute('data-dose');"
        "var time=r.getAttribute('data-time');"
        "var sel=r.querySelector('select');"
        "rows.push({name:name,dose:dose,time:time,status:sel?sel.value:''});"
        "});"
        f"wlApi('mar_save', {{patient_id:{selected_patient_json}, mar_items:rows, current_page:'vitals_mar'}});"
        "})();"
    )
    vitals_btn_cls = "pill-btn primary"
    mar_btn_cls = "pill-btn primary"
    vitals_btn_dis = ""
    mar_btn_dis = ""

    mar_rows = ""
    for m in mar_items:
        mar_rows += f"""
<div class='mar-row' data-name="{html.escape(str(m.get('name','')))}" data-dose="{html.escape(str(m.get('dose','')))}" data-time="{html.escape(str(m.get('time','')))}">
  <div>{html.escape(str(m.get('name','')))}</div>
  <div>{html.escape(str(m.get('dose','')))}</div>
  <div>{html.escape(str(m.get('time','')))}</div>
  <div>
    <select onchange="wlEnableSaveBtn('save_mar_btn')">
      <option {'selected' if str(m.get('status','')).lower()=='given' else ''}>Given</option>
      <option {'selected' if str(m.get('status','')).lower()=='delayed' else ''}>Delayed</option>
      <option {'selected' if str(m.get('status','')).lower()=='refused' else ''}>Refused</option>
      <option {'selected' if str(m.get('status','')).lower()=='due' else ''}>Due</option>
    </select>
  </div>
</div>
"""
    alerts_html = "".join(f"<li>{html.escape(a)}</li>" for a in alerts)
    recent_html = ""
    for r in recent:
        recent_html += f"""
<div class='recent-row'>
  <div>{html.escape(str(r.get('time','')))}</div>
  <div>{html.escape(str(r.get('vitals','')))}</div>
  <div>{html.escape(str(r.get('meds','')))}</div>
</div>
"""
    if not recent_html:
        recent_html = "<div class='empty'>No recent records.</div>"

    return f"""
<div class='vitals-mar-page'>
  {_page_header('Vitals & MAR', 'Record observations and medication administration in one place.')}
  <div class='staff-topbar'>
    <div class='patient-picker'>
      <select onchange="var opt=this.selectedOptions[0]; var bed=opt?opt.getAttribute('data-bed'):''; wlApi('nurse_select_patient', {{patient_id:this.value, bed_id:bed, current_page:'vitals_mar'}});">
        {options_html}
      </select>
    </div>
    <div class='staff-meta'>{html.escape(picker.get('ward_label','Ward A'))} - Staff {html.escape(state.get('staff_id') or '')}</div>
  </div>
  <div class='split-cards'>
    <div class='card'>
      <div class='card-title nurse-subtitle'>Patient Details</div>
      <div class='patient-details'>
        <div class='patient-main'>{html.escape(str(patient.get('bed_id','')))}</div>
        <div class='patient-sub'>{html.escape(str(patient.get('patient_id','')))}</div>
        <div class='patient-meta'>Age/Sex: {html.escape(str(patient.get('age','--')))}{html.escape(str(patient.get('sex','')))}</div>
        <div class='patient-tags'>
          <span class='tag tag-teal'>Allergy: {html.escape(str(patient.get('allergy','--')))}</span>
          <span class='tag tag-lime'>Risk: {html.escape(str(patient.get('risk','--')))}</span>
        </div>
        <div class='patient-updated'>Last updated: {html.escape(str(patient.get('updated_at','--')))}</div>
      </div>
    </div>
    <div class='card'>
      <div class='card-title nurse-subtitle'>Quick Alerts</div>
      <ul class='alert-list vitals-alert-list'>{alerts_html}</ul>
    </div>
  </div>
  <div class='split-cards entry-cards'>
    <div class='card'>
      <div class='card-title'>Record Vitals</div>
      <div class='form-grid'>
        <label>Temperature (C)<input id='v_temp' oninput="wlEnableSaveBtn('save_vitals_btn')" value='{html.escape(str(vitals.get('temperature_c','')))}' /></label>
        <label>Heart Rate (bpm)<input id='v_hr' oninput="wlEnableSaveBtn('save_vitals_btn')" value='{html.escape(str(vitals.get('heart_rate','')))}' /></label>
        <label>Resp. Rate (breaths/min)<input id='v_rr' oninput="wlEnableSaveBtn('save_vitals_btn')" value='{html.escape(str(vitals.get('resp_rate','')))}' /></label>
        <label>BP (mmHg)<input id='v_bp' oninput="wlEnableSaveBtn('save_vitals_btn')" value='{html.escape(str(vitals.get('bp','')))}' /></label>
        <label>SpO2 (%)<input id='v_spo2' oninput="wlEnableSaveBtn('save_vitals_btn')" value='{html.escape(str(vitals.get('spo2','')))}' /></label>
        <label>Pain Score (0-10)<input id='v_pain' oninput="wlEnableSaveBtn('save_vitals_btn')" value='{html.escape(str(vitals.get('pain','')))}' /></label>
      </div>
      <div class='form-actions'>
        <button id='save_vitals_btn' class='{vitals_btn_cls}' {vitals_btn_dis} onclick="{html.escape(vitals_save_js, quote=True)}">Save Vitals</button>
      </div>
    </div>
    <div class='card mar-card'>
      <div class='card-title'>Medication Administration</div>
      <div class='mar-table'>
        <div class='mar-head'><div>Medication</div><div>Dose</div><div>Time</div><div>Status</div></div>
        {mar_rows}
      </div>
      <div class='form-actions right'>
        <button id='save_mar_btn' class='{mar_btn_cls}' {mar_btn_dis} onclick="{html.escape(mar_save_js, quote=True)}">Save MAR</button>
      </div>
    </div>
  </div>
  <div class='card'>
    <div class='card-title'>Recent Records</div>
    <div class='subtle-note'>Showing up to the 10 most recent records.</div>
    <div class='recent-head'><div>Time</div><div>Vitals Summary</div><div>Meds Summary</div></div>
    {recent_html}
  </div>
</div>
"""


def _render_assessment(state: dict, ctx: dict) -> str:
    data = ctx["get_assessment_data"](state)
    picker = data.get("picker", {})
    options_html = "".join(
        f"<option value='{o['value']}' {'selected' if o['value']==picker.get('selected') else ''}>{html.escape(o['label'])}</option>"
        for o in picker.get("options", [])
    )
    sources = data.get("sources", {})
    note = data.get("note", "")
    result = data.get("result") or {}
    diag = (
        result.get("diagnosis_json")
        or result.get("diagnosis")
        or (result.get("result_struct") or {}).get("diagnosis_json")
        or {}
    )
    summary = (
        _clinical_summary_from_diag(diag)
        or "Assessment generated."
    )
    key_changes = diag.get("key_changes") if isinstance(diag.get("key_changes"), list) else []
    treatment_suggestions = diag.get("treatment_suggestions") if isinstance(diag.get("treatment_suggestions"), list) else []
    red_flags = diag.get("red_flags") if isinstance(diag.get("red_flags"), list) else []
    primary_dx = str(diag.get("primary_diagnosis") or "").strip() or "Not specified"
    risk_level = str(diag.get("risk_level") or "").strip() or "Not specified"
    confidence = diag.get("confidence_score")
    confidence_text = str(confidence) if confidence is not None else "Not provided"
    gaps = result.get("gaps") or []
    tool_trace = result.get("tool_trace") or []
    evidence = result.get("rag_evidence") or []
    status_msg = data.get("status_msg") or ""
    edit_text = str(data.get("edit_text") or "").strip()
    has_assessment = bool(result)
    if not has_assessment:
        summary = "No assessment yet. Click Generate / Update Assessment to create one."
        edit_text = ""
    selected_patient_json = json.dumps(picker.get("selected"))
    selected_patient_label = str(picker.get("selected") or "--")
    for option in picker.get("options", []):
        if str(option.get("value")) == str(picker.get("selected")):
            selected_patient_label = str(option.get("label") or selected_patient_label)
            break

    summary_preview = str(summary or "").strip()
    if has_assessment and summary_preview:
        summary_list_html = f"<li>{html.escape(summary_preview)}</li>"
    else:
        summary_list_html = "<li>Generate an assessment to see key highlights.</li>"
    key_changes_html = "".join(f"<li>{html.escape(str(c))}</li>" for c in key_changes[:6]) or "<li>No key changes extracted.</li>"
    suggestions_html = "".join(
        f"<li>{html.escape(str(item))}</li>" for item in treatment_suggestions[:6]
    ) or "<li>No suggested actions.</li>"
    red_flags_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in red_flags[:6]) or "<li>No red flags listed.</li>"
    details_disabled_attr = "disabled" if not has_assessment else ""

    note_input_js = (
        "wlDebounceAction('assessment_note', function(){"
        "return {note: this.value || '', current_page: 'generate_assessment'};"
        "}.bind(this), 500);"
    )
    note_blur_js = (
        "wlFlushDebounce('assessment_note', function(){"
        "return {note: this.value || '', current_page: 'generate_assessment'};"
        "}.bind(this));"
    )
    generate_js = html.escape(
        "(async function(){"
        "var ta=document.getElementById('nurse_note_input');"
        "var btn=document.getElementById('assessment_generate_btn');"
        "var status=document.getElementById('assessment_generate_status');"
        f"var pid={selected_patient_json};"
        "var note=ta?ta.value:'';"
        "if(btn){btn.disabled=true; btn.classList.add('is-busy'); btn.textContent='Generating...';}"
        "if(status){status.textContent='Running assessment ...'; status.classList.add('show'); status.classList.add('running');}"
        "wlFlushDebounce('assessment_note');"
        "try{"
        "await wlApi('assessment_generate', {patient_id: pid, note: note, current_page: 'generate_assessment'});"
        "}catch(e){"
        "if(status){status.textContent='Request failed. Please retry.'; status.classList.remove('running'); status.classList.add('show');}"
        "if(btn){btn.disabled=false; btn.classList.remove('is-busy'); btn.textContent='Generate / Update Assessment';}"
        "}"
        "})(); return false;",
        quote=True,
    )
    details_open_js = html.escape(
        "(function(){"
        "try{localStorage.setItem('wl_assessment_detail_open','1');}catch(e){}"
        "var m=document.getElementById('assessment_detail_modal');"
        "if(m){m.style.display='flex';}"
        "})(); return false;",
        quote=True,
    )
    details_close_js = html.escape(
        "(function(){"
        "try{localStorage.removeItem('wl_assessment_detail_open');}catch(e){}"
        "var m=document.getElementById('assessment_detail_modal');"
        "if(m){m.style.display='none';}"
        "})(); return false;",
        quote=True,
    )
    save_draft_js = html.escape(
        "(async function(){"
        "var ta=document.getElementById('assessment_edit_text');"
        "var txt=ta?ta.value:'';"
        f"var pid={selected_patient_json};"
        "try{localStorage.setItem('wl_assessment_detail_open','1');}catch(e){}"
        "await wlApi('assessment_edit_save', {patient_id: pid, text: txt, current_page: 'generate_assessment'});"
        "})(); return false;",
        quote=True,
    )
    send_patient_js = html.escape(
        "(async function(){"
        "var ta=document.getElementById('assessment_edit_text');"
        "var txt=ta?ta.value:'';"
        f"var pid={selected_patient_json};"
        "try{localStorage.setItem('wl_assessment_detail_open','1');}catch(e){}"
        "await wlApi('assessment_send_patient', {patient_id: pid, text: txt, current_page: 'generate_assessment'});"
        "})(); return false;",
        quote=True,
    )

    image_name = data.get("image_name") or "Add image (optional)"
    audio_name = data.get("audio_name") or "Add audio (optional)"

    gaps_html = "".join(
        f"<li>{html.escape(_to_english_gap(g.get('message') if isinstance(g, dict) else g))}</li>" for g in gaps[:5]
    ) or "<li>No major gaps detected.</li>"
    evidence_html = ""
    for i, e in enumerate(evidence[:4], start=1):
        score = e.get("score")
        score_text = "Evidence match" if score is None else f"Evidence match: {int(float(score)*100)}%"
        evidence_html += f"""
<div class='evidence-item'>
  <div class='evidence-title'>Snippet {i}: {html.escape(str(e.get('source_file','Evidence')))}</div>
  <div class='evidence-snippet'>{html.escape(str(e.get('snippet','')))}</div>
  <div class='evidence-score'>{score_text}</div>
</div>
"""
    if not evidence_html:
        evidence_html = "<div class='empty'>No evidence snippets.</div>"

    trace_html = ""
    for t in tool_trace[:6]:
        status = "Completed" if t.get("success") else "Fallback used"
        trace_html += f"""
<div class='trace-row'>
  <div>{html.escape(str(t.get('step','')))}</div>
  <div class='trace-status'>{status}</div>
  <div>{html.escape(str(t.get('latency_ms','--')))} ms</div>
  <div>{html.escape(str(t.get('summary','')))}</div>
</div>
"""
    if not trace_html:
        trace_html = "<div class='empty'>No tool trace available.</div>"

    return f"""
<div class='generate-assessment-page'>
  {_page_header('Generate Assessment', 'Review inputs and generate an updated clinical assessment.')}
  <div class='staff-topbar'>
    <div class='patient-picker'>
      <select onchange="wlApi('nurse_select_patient', {{patient_id:this.value, current_page:'generate_assessment'}});">
        {options_html}
      </select>
    </div>
    <div class='staff-meta'>{html.escape(picker.get('ward_label','Ward A'))} - Staff {html.escape(state.get('staff_id') or '')}</div>
  </div>
  <div class='split-cards assessment-top-cards'>
    <div class='card'>
      <div class='card-title nurse-subtitle'>Input Sources</div>
      <div class='chip-row'>
        <span class='chip'>{'Latest Daily Log' if sources.get('daily_log') == 'Available' else 'Daily Log missing'}</span>
        <span class='chip'>{'Latest Vitals' if sources.get('vitals') == 'Available' else 'Vitals missing'}</span>
        <span class='chip'>{'Latest MAR' if sources.get('mar') == 'Available' else 'MAR missing'}</span>
        <span class='chip'>{'Last Assessment' if sources.get('assessment') == 'Available' else 'No assessment yet'}</span>
      </div>
    </div>
    <div class='card'>
      <div class='card-title nurse-subtitle'>Optional inputs</div>
      <div class='nurse-note-wrap'>
        <textarea id='nurse_note_input' class='nurse-note-input' rows='3' placeholder='Nurse note'
          oninput="{html.escape(note_input_js, quote=True)}"
          onblur="{html.escape(note_blur_js, quote=True)}">{html.escape(note)}</textarea>
      </div>
      <div class='attach-row'>
        <button class='pill-btn' onclick="document.getElementById('assess_audio').click();">{html.escape(audio_name)}</button>
        <button class='pill-btn' onclick="document.getElementById('assess_image').click();">{html.escape(image_name)}</button>
        <input id='assess_audio' type='file' accept='audio/*' style='display:none'
          onchange="var fd=new FormData(); fd.append('file', this.files[0]); wlApiUpload('/api/assessment_audio', fd); this.value='';" />
        <input id='assess_image' type='file' accept='image/*' style='display:none'
          onchange="var fd=new FormData(); fd.append('file', this.files[0]); wlApiUpload('/api/assessment_image', fd); this.value='';" />
      </div>
    </div>
  </div>
  <div class='generate-bar'>
    <button id='assessment_generate_btn' class='generate-btn' onclick="{generate_js}">Generate / Update Assessment</button>
    <div id='assessment_generate_status' class='generate-status {'show' if status_msg else ''}'>{html.escape(status_msg)}</div>
  </div>
  <div class='split-cards'>
    <div class='card'>
      <div class='card-title nurse-subtitle'>Assessment Summary</div>
      <div class='summary-text'>{html.escape(summary)}</div>
      <div class='key-changes'>
        <div class='sub-title'>Preview</div>
        <ul class='assessment-summary-list'>{summary_list_html}</ul>
      </div>
      <div class='assessment-summary-actions'>
        <button class='pill-btn' {details_disabled_attr} onclick="{details_open_js}">See details</button>
      </div>
    </div>
    <div class='card'>
      <div class='card-title nurse-subtitle'>Gaps</div>
      <ul class='gap-list'>{gaps_html}</ul>
    </div>
  </div>
  <div class='split-cards stack-cards'>
    <div class='card'>
      <div class='card-title nurse-subtitle'>Evidence</div>
      {evidence_html}
    </div>
    <div class='card'>
      <div class='card-title nurse-subtitle'>Tool Trace</div>
      <div class='trace-head'><div>Tool</div><div>Status</div><div>Latency</div><div>Note</div></div>
      {trace_html}
    </div>
  </div>
</div>
<div id='assessment_detail_modal' class='care-modal-backdrop assessment-detail-modal' style='display:none' onclick="{details_close_js}">
  <div class='care-modal assessment-modal' onclick="event.stopPropagation();">
    <div class='care-modal-scroll'>
    <h3>Assessment Details</h3>
    <div class='care-modal-date'>{html.escape(selected_patient_label)}</div>
    <div class='assessment-meta-grid'>
      <div class='assessment-meta-item'>
        <div class='sub-title'>Primary diagnosis</div>
        <div>{html.escape(primary_dx)}</div>
      </div>
      <div class='assessment-meta-item'>
        <div class='sub-title'>Risk level</div>
        <div>{html.escape(risk_level)}</div>
      </div>
      <div class='assessment-meta-item'>
        <div class='sub-title'>Confidence</div>
        <div>{html.escape(confidence_text)}</div>
      </div>
    </div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Summary</div>
      <div class='summary-text'>{html.escape(summary)}</div>
    </div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Key changes</div>
      <ul>{key_changes_html}</ul>
    </div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Recommended actions</div>
      <ul>{suggestions_html}</ul>
    </div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Watch-outs</div>
      <ul>{red_flags_html}</ul>
    </div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Editable message to patient</div>
      <textarea id='assessment_edit_text' class='assessment-edit-textarea' rows='10' placeholder='Edit assessment before sending'>{html.escape(edit_text)}</textarea>
    </div>
    <div class='care-modal-actions assessment-modal-actions'>
      <button class='care-action' {details_disabled_attr} onclick="{save_draft_js}">Save Draft</button>
      <button class='care-action care-action-primary' {details_disabled_attr} onclick="{send_patient_js}">Send to Patient</button>
      <button class='care-action care-action-secondary' onclick="{details_close_js}">Close</button>
    </div>
    </div>
  </div>
</div>
<script>
(function(){{
  try {{
    if (localStorage.getItem('wl_assessment_detail_open') === '1') {{
      var modal = document.getElementById('assessment_detail_modal');
      if (modal) {{
        modal.style.display = 'flex';
      }}
    }}
  }} catch (e) {{}}
}})();
</script>
"""


def _parse_sbar_items(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            parsed = ast.literal_eval(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
    if ";" in raw:
        parts = [p.strip(" -") for p in raw.split(";")]
        cleaned = [p for p in parts if p]
        if len(cleaned) > 1:
            return cleaned
    return [raw]


def _strip_markdown_tokens(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n")
    if not raw.strip():
        return ""
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", raw)
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"^\s{0,3}#{1,6}\s*", "", cleaned, flags=re.M)
    cleaned = re.sub(r"^\s{0,3}>\s?", "", cleaned, flags=re.M)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(lines).strip()


def _parse_sbar(sbar_md: str) -> dict:
    sections = {"S": [], "B": [], "A": [], "R": []}
    current = None
    short_inline_re = re.compile(r"^([SBAR])\s*(?:\([^)]*\))?\s*:\s*(.*)$", re.I)
    short_only_re = re.compile(r"^([SBAR])\s*(?:\([^)]*\))?\s*$", re.I)
    word_inline_re = re.compile(r"^(Situation|Background|Assessment|Recommendation)\s*:\s*(.*)$", re.I)
    word_only_re = re.compile(r"^(Situation|Background|Assessment|Recommendation)\s*$", re.I)
    word_to_key = {
        "situation": "S",
        "background": "B",
        "assessment": "A",
        "recommendation": "R",
    }

    def _match_header(line_text: str):
        plain = re.sub(r"\*\*(.*?)\*\*", r"\1", str(line_text or "")).strip()
        plain = plain.lstrip("- ").strip()
        m = short_inline_re.match(plain)
        if m:
            return str(m.group(1)).upper(), str(m.group(2) or "").strip()
        m = short_only_re.match(plain)
        if m:
            return str(m.group(1)).upper(), ""
        m = word_inline_re.match(plain)
        if m:
            key = word_to_key.get(str(m.group(1) or "").strip().lower())
            if key:
                return key, str(m.group(2) or "").strip()
        m = word_only_re.match(plain)
        if m:
            key = word_to_key.get(str(m.group(1) or "").strip().lower())
            if key:
                return key, ""
        return None, ""

    for line in (sbar_md or "").splitlines():
        text = line.strip()
        if not text:
            continue
        key, remainder = _match_header(text)
        if key in sections:
            current = key
            if remainder:
                sections[current].extend(_parse_sbar_items(remainder))
            continue
        if text.startswith("-") and current in sections:
            item = text.lstrip("- ").strip()
            if item:
                sections[current].extend(_parse_sbar_items(item))
            continue
        if current in sections:
            clean = re.sub(r"^\*+|\*+$", "", text).strip()
            if clean:
                sections[current].extend(_parse_sbar_items(clean))
    return sections


def _render_handover(state: dict, ctx: dict) -> str:
    data = ctx["get_handover_data"](state)
    picker = data.get("picker", {})
    options_html = "".join(
        f"<option value='{o['value']}' {'selected' if o['value']==picker.get('selected') else ''}>{html.escape(o['label'])}</option>"
        for o in picker.get("options", [])
    )
    sbar_md = str(data.get("sbar_md") or "")
    sections = _parse_sbar(sbar_md)
    key_points = data.get("key_points") if isinstance(data.get("key_points"), list) else []
    status_msg = str(data.get("status_msg") or "").strip()
    forward_status_msg = str(data.get("forward_status_msg") or "").strip()
    target_staff_id = str(data.get("target_staff_id") or "").strip()
    forward_text_raw = str(data.get("forward_text") or "").strip()
    if forward_text_raw:
        forward_text = _strip_markdown_tokens(forward_text_raw)
    else:
        plain_lines: list[str] = []
        for key, label in [("S", "Situation"), ("B", "Background"), ("A", "Assessment"), ("R", "Recommendation")]:
            items = [str(x).strip() for x in (sections.get(key) or []) if str(x).strip()]
            if not items:
                continue
            joined = "; ".join(_strip_markdown_tokens(item) for item in items if _strip_markdown_tokens(item))
            if joined:
                plain_lines.append(f"{label}: {joined}")
        forward_text = "\n".join(plain_lines).strip() or _strip_markdown_tokens(sbar_md)
    forward_audio_name = str(data.get("forward_audio_name") or "Add audio (optional)")
    forward_image_name = str(data.get("forward_image_name") or "Add image (optional)")
    has_sbar = bool(sbar_md.strip()) or any(bool(sections.get(k)) for k in ("S", "B", "A", "R"))
    details_disabled_attr = "disabled" if not has_sbar else ""

    def _section_items_html(items: list[str]) -> str:
        if not items:
            return "<div class='empty'>No items.</div>"
        cleaned_items = [_strip_markdown_tokens(item).strip() for item in items if str(item).strip()]
        if not cleaned_items:
            return "<div class='empty'>No items.</div>"
        return "<ul class='handover-section-list'>{}</ul>".format(
            "".join(f"<li>{html.escape(item)}</li>" for item in cleaned_items)
        )

    preview_items: list[str] = []
    for key, label in [("S", "Situation"), ("B", "Background"), ("A", "Assessment"), ("R", "Recommendation")]:
        items = sections.get(key) or []
        if items:
            preview_items.append(f"{label}: {_strip_markdown_tokens(items[0])}")
    if len(preview_items) < 3:
        for point in key_points:
            text = _strip_markdown_tokens(point).strip()
            if not text:
                continue
            if text in preview_items:
                continue
            preview_items.append(text)
            if len(preview_items) >= 4:
                break
    preview_items = preview_items[:4]
    preview_html = "".join(f"<li>{html.escape(item)}</li>" for item in preview_items) or "<li>No items.</li>"

    tabs_html = ""
    for t in ["Today", "Last 3 days"]:
        active = "active" if t == data.get("range") else ""
        tabs_html += f"<button class='tab {active}' onclick=\"{_action_js('handover_range', {'range': t})}\">{t}</button>"

    sbar_text = json.dumps(sbar_md)
    selected_patient_json = json.dumps(picker.get("selected"))
    selected_patient_label = str(picker.get("selected") or "--")
    for option in picker.get("options", []):
        if str(option.get("value")) == str(picker.get("selected")):
            selected_patient_label = str(option.get("label") or selected_patient_label)
            break
    generate_sbar_js = html.escape(
        "(async function(){"
        "var btn=document.getElementById('sbar_generate_btn');"
        "var status=document.getElementById('sbar_generate_status');"
        f"var payload={{patient_id:{selected_patient_json}, current_page:'handover_summary'}};"
        "if(btn){btn.disabled=true; btn.classList.add('is-busy'); btn.textContent='Generating SBAR...';}"
        "if(status){status.textContent='Generating SBAR in background...'; status.classList.add('show'); status.classList.add('running');}"
        "try{await wlApi('handover_generate', payload);}catch(e){"
        "if(status){status.textContent='SBAR request failed. Please retry.'; status.classList.remove('running'); status.classList.add('show');}"
        "if(btn){btn.disabled=false; btn.classList.remove('is-busy'); btn.textContent='Generate SBAR';}"
        "}"
        "})(); return false;",
        quote=True,
    )
    details_open_js = html.escape(
        "(function(){"
        "try{localStorage.setItem('wl_handover_detail_open','1');}catch(e){}"
        "var m=document.getElementById('handover_detail_modal');"
        "if(m){m.style.display='flex';}"
        "})(); return false;",
        quote=True,
    )
    details_close_js = html.escape(
        "(function(){"
        "try{localStorage.removeItem('wl_handover_detail_open');}catch(e){}"
        "var m=document.getElementById('handover_detail_modal');"
        "if(m){m.style.display='none';}"
        "})(); return false;",
        quote=True,
    )
    forward_js = html.escape(
        "(async function(){"
        "var target=document.getElementById('handover_target_staff_id');"
        "var targetId=(target&&target.value)?target.value.trim():'';"
        "var msgEl=document.getElementById('handover_forward_text');"
        "var msg=(msgEl&&msgEl.value)?msgEl.value.trim():'';"
        f"var pid={selected_patient_json};"
        f"var sbar={sbar_text};"
        "if(!targetId){wlShowToast('Enter target nurse ID.'); return false;}"
        "try{localStorage.setItem('wl_handover_detail_open','1');}catch(e){}"
        "await wlApi('handover_forward', {patient_id:pid, target_staff_id:targetId, sbar_md:sbar, forward_text:msg, current_page:'handover_summary'});"
        "})(); return false;",
        quote=True,
    )
    raw_sbar_fallback = ""
    if has_sbar and not any(bool(sections.get(k)) for k in ("S", "B", "A", "R")):
        raw_sbar_fallback = (
            "<div class='assessment-detail-section'>"
            "<div class='sub-title'>Raw SBAR</div>"
            f"<pre class='sbar-raw'>{html.escape(_strip_markdown_tokens(sbar_md))}</pre>"
            "</div>"
        )

    return f"""
<div>
  {_page_header('Handover Summary', 'Prepare SBAR handover and align next-shift actions.')}
  <div class='staff-topbar'>
    <div class='patient-picker'>
      <select onchange="wlApi('nurse_select_patient', {{patient_id:this.value, current_page:'handover_summary'}});">
        {options_html}
      </select>
    </div>
    <div class='staff-meta'>{html.escape(picker.get('ward_label','Ward A'))} - Staff {html.escape(state.get('staff_id') or '')}</div>
  </div>
  <div class='handover-toolbar'>
    <div class='range-tabs'>
      {tabs_html}
    </div>
    <div class='handover-generate-wrap'>
      <button id='sbar_generate_btn' class='generate-btn' onclick="{generate_sbar_js}">Generate SBAR</button>
      <div id='sbar_generate_status' class='generate-status {'show' if status_msg else ''}'>{html.escape(status_msg)}</div>
    </div>
  </div>
  <div class='card'>
    <div class='card-title'>SBAR Handover Summary</div>
    <div class='sub-title'>Preview</div>
    <ul class='assessment-summary-list handover-preview-list'>{preview_html}</ul>
    <div class='assessment-summary-actions'>
      <button class='pill-btn' {details_disabled_attr} onclick="{details_open_js}">See details</button>
    </div>
  </div>
</div>
<div id='handover_detail_modal' class='care-modal-backdrop handover-detail-modal' style='display:none' onclick="{details_close_js}">
  <div class='care-modal handover-modal' onclick="event.stopPropagation();">
    <div class='care-modal-scroll'>
    <h3>SBAR Details</h3>
    <div class='care-modal-date'>{html.escape(selected_patient_label)}</div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Situation</div>
      {_section_items_html(sections.get('S', []))}
    </div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Background</div>
      {_section_items_html(sections.get('B', []))}
    </div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Assessment</div>
      {_section_items_html(sections.get('A', []))}
    </div>
    <div class='assessment-detail-section'>
      <div class='sub-title'>Recommendation</div>
      {_section_items_html(sections.get('R', []))}
    </div>
    {raw_sbar_fallback}
    <div class='assessment-detail-section'>
      <div class='sub-title'>Forward to another nurse</div>
      <div class='auto-summary'>Edit before forwarding (original SBAR text is prefilled).</div>
      <textarea id='handover_forward_text' class='request-assessment-textarea' rows='8' placeholder='Editable forwarding message'>{html.escape(forward_text)}</textarea>
      <div class='attach-row'>
        <button class='pill-btn' onclick="document.getElementById('handover_forward_audio').click();">{html.escape(forward_audio_name)}</button>
        <button class='pill-btn' onclick="document.getElementById('handover_forward_image').click();">{html.escape(forward_image_name)}</button>
        <input id='handover_forward_audio' type='file' accept='audio/*' style='display:none'
          onchange="var f=this.files&&this.files[0]; if(!f){{return;}} try{{localStorage.setItem('wl_handover_detail_open','1');}}catch(e){{}} var fd=new FormData(); fd.append('file', f); var txt=document.getElementById('handover_forward_text'); if(txt){{fd.append('forward_text', txt.value||'');}} var tgt=document.getElementById('handover_target_staff_id'); if(tgt){{fd.append('target_staff_id', tgt.value||'');}} wlApiUpload('/api/handover_forward_audio', fd); this.value='';" />
        <input id='handover_forward_image' type='file' accept='image/*' style='display:none'
          onchange="var f=this.files&&this.files[0]; if(!f){{return;}} try{{localStorage.setItem('wl_handover_detail_open','1');}}catch(e){{}} var fd=new FormData(); fd.append('file', f); var txt=document.getElementById('handover_forward_text'); if(txt){{fd.append('forward_text', txt.value||'');}} var tgt=document.getElementById('handover_target_staff_id'); if(tgt){{fd.append('target_staff_id', tgt.value||'');}} wlApiUpload('/api/handover_forward_image', fd); this.value='';" />
      </div>
      <div class='handover-forward-row'>
        <input id='handover_target_staff_id' class='handover-forward-input' type='text' value='{html.escape(target_staff_id)}' placeholder='Target staff ID (e.g., N-05288)' />
        <button class='care-action care-action-primary' {details_disabled_attr} onclick="{forward_js}">Forward</button>
      </div>
      <div class='request-assessment-status {'show' if forward_status_msg else ''}'>{html.escape(forward_status_msg)}</div>
    </div>
    <div class='care-modal-actions assessment-modal-actions'>
      <button class='care-action care-action-secondary' onclick="{details_close_js}">Close</button>
    </div>
    </div>
  </div>
</div>
<script>
(function(){{
  try {{
    if (localStorage.getItem('wl_handover_detail_open') === '1') {{
      var modal = document.getElementById('handover_detail_modal');
      if (modal) {{
        modal.style.display = 'flex';
      }}
    }}
  }} catch (e) {{}}
}})();
</script>
"""


def _render_inbox(state: dict, ctx: dict) -> str:
    data = ctx["get_inbox_data"](state)
    requests = data.get("requests", [])
    selected = data.get("selected")
    filter_tab = data.get("filter", "Pending")
    source_filter = data.get("source_filter", "All")
    search = data.get("search", "")
    forward_doctor_id = str(data.get("forward_doctor_id") or "").strip()
    forward_status_msg = str(data.get("forward_status_msg") or "").strip()
    tabs = ""
    for t in ["Pending", "In Progress", "Done"]:
        active = "active" if t == filter_tab else ""
        tabs += f"<button class='tab {active}' onclick=\"{_action_js('requests_filter', {'filter': t})}\">{t}</button>"
    source_tabs = ""
    for t in ["All", "Patient", "Nurse", "Doctor"]:
        active = "active" if t == source_filter else ""
        source_tabs += f"<button class='tab {active}' onclick=\"{_action_js('requests_source_filter', {'source_filter': t})}\">{t}</button>"
    search_js = _action_js("requests_search", {"q": "__Q__"}).replace("__Q__", "'+val+'")
    list_html = ""
    for r in requests:
        type_badge = "<span class='tag request-type-badge'>Forwarded Handover</span>" if r.get("is_forwarded_handover") else ""
        source_badge = f"<span class='tag request-source-badge'>From {html.escape(str(r.get('source_category') or 'Patient'))}</span>"
        tags = "".join(f"<span class='tag tag-lime'>{html.escape(t)}</span>" for t in r.get("tags", [])[:2])
        list_html += f"""
<div class='request-item' onclick="{_action_js('requests_select', {'request_id': r.get('request_id')})}">
  <div class='request-title'>Bed {html.escape(str(r.get('bed_id','')))} | {html.escape(str(r.get('patient_id','')))}</div>
  <div class='request-meta'>Created: {html.escape(str(r.get('created_at',''))[:16])}</div>
  <div class='request-summary'>{html.escape(r.get('summary',''))}</div>
  <div class='request-tags'>{source_badge}{type_badge}{tags}</div>
</div>
"""
    if not list_html:
        list_html = "<div class='empty'>No requests.</div>"

    detail_html = "<div class='empty'>Select a request</div>"
    if selected:
        selected_id = str(selected.get("request_id") or "")
        drafts = state.get("requests_assessment_drafts") or {}
        draft_text = str(drafts.get(selected_id) or "").strip()
        status_msg = ""
        if str(state.get("requests_assessment_status_request_id") or "") == selected_id:
            status_msg = str(state.get("requests_assessment_status_msg") or "").strip()
        selected_id_json = json.dumps(selected_id)
        patient_id_json = json.dumps(selected.get("patient_id"))
        generate_js = html.escape(
            "(async function(){"
            "var btn=document.getElementById('request_generate_btn');"
            "var st=document.getElementById('request_assessment_status');"
            "if(btn){btn.disabled=true;btn.classList.add('is-busy');btn.textContent='Generating...';}"
            "if(st){st.textContent='Generating assessment draft...';st.classList.add('show');}"
            f"await wlApi('requests_generate', {{request_id:{selected_id_json}, current_page:'nurse_inbox'}});"
            "})(); return false;",
            quote=True,
        )
        save_draft_js = html.escape(
            "(async function(){"
            "var ta=document.getElementById('request_assessment_text');"
            "var txt=ta?ta.value:'';"
            f"await wlApi('requests_assessment_draft', {{request_id:{selected_id_json}, text:txt, current_page:'nurse_inbox'}});"
            "})(); return false;",
            quote=True,
        )
        send_js = html.escape(
            "(async function(){"
            "var ta=document.getElementById('request_assessment_text');"
            "var txt=ta?ta.value:'';"
            f"await wlApi('requests_assessment_send', {{request_id:{selected_id_json}, patient_id:{patient_id_json}, text:txt, current_page:'nurse_inbox'}});"
            "})(); return false;",
            quote=True,
        )
        forward_doctor_js = html.escape(
            "(async function(){"
            "var input=document.getElementById('request_forward_doctor_id');"
            "var doctorId=input?String(input.value||'').trim():'';"
            "if(!doctorId){wlShowToast('Enter doctor staff ID first.'); if(input){input.focus();} return false;}"
            f"await wlApi('requests_forward_doctor', {{request_id:{selected_id_json}, doctor_staff_id:doctorId, current_page:'nurse_inbox'}});"
            "})(); return false;",
            quote=True,
        )
        audio_url = str(selected.get("audio_path") or "").strip()
        audio_html = "<div class='audio-player empty'>No audio</div>"
        if audio_url:
            audio_html = (
                f"<audio class='audio-player-el' controls preload='none' src='{html.escape(audio_url, quote=True)}'></audio>"
            )

        image_items = []
        for u in selected.get("images") or []:
            url = str(u or "").strip()
            if not url:
                continue
            image_items.append(
                "<a class='thumb-link' href='{0}' target='_blank' rel='noopener noreferrer'>"
                "<img class='thumb-img' src='{0}' alt='request image' />"
                "</a>".format(html.escape(url, quote=True))
            )
        images = "".join(image_items) if image_items else "<div class='attachments-empty'>No images</div>"
        forwarded_badge = ""
        forwarded_meta = ""
        if selected.get("is_forwarded_handover"):
            forwarded_badge = "<span class='tag request-type-badge detail-type-badge'>Forwarded Handover</span>"
            from_staff = str(selected.get("forward_from") or "").strip()
            to_staff = str(selected.get("forward_to") or "").strip()
            if from_staff or to_staff:
                from_text = html.escape(from_staff or "Unknown")
                to_text = html.escape(to_staff or "Unknown")
                forwarded_meta = (
                    "<div class='detail-forward-meta'>"
                    f"From {from_text} to {to_text}"
                    "</div>"
                )
        detail_html = f"""
<div class='detail-card'>
  <div class='detail-type-row'>{forwarded_badge}</div>
  <div class='detail-title'>Request Detail</div>
  {forwarded_meta}
  <div class='detail-source'>Source: {html.escape(str(selected.get('source_category') or 'Patient'))}</div>
  <div class='detail-info'>Patient Info: Bed {html.escape(str(selected.get('bed_id','')))} | {html.escape(str(selected.get('patient_id','')))}</div>
  <div class='detail-status'>Status: {html.escape(str(selected.get('status','Pending')).title())}</div>
  <div class='detail-section'>
    <div class='sub-title'>Full Symptom Summary</div>
    <div>{html.escape(selected.get('detail',''))}</div>
  </div>
  <div class='detail-section'>
    <div class='sub-title'>Last Chat Summary Preview (optional)</div>
    <div>{html.escape(selected.get('chat_summary',''))}</div>
  </div>
  <div class='detail-section'>
    <div class='sub-title'>Attachments</div>
    <div class='attachments'>
      {audio_html}
      <div class='image-grid'>{images}</div>
    </div>
    <div class='auto-summary'>Auto summary from attachments (optional)</div>
  </div>
  <div class='detail-section request-assessment-panel'>
    <div class='sub-title'>Patient Message Draft</div>
    <div class='auto-summary'>Generate first, then edit. Send only when ready.</div>
    <textarea id='request_assessment_text' class='request-assessment-textarea' rows='8' placeholder='Generated draft will appear here.'>{html.escape(draft_text)}</textarea>
    <div class='detail-actions'>
      <button id='request_generate_btn' class='generate-btn' onclick="{generate_js}">Generate Draft</button>
      <button class='pill-btn' onclick="{save_draft_js}">Keep Draft (Not Send)</button>
      <button class='pill-btn primary' onclick="{send_js}">Send to Patient Inbox</button>
    </div>
    <div id='request_assessment_status' class='request-assessment-status {'show' if status_msg else ''}'>{html.escape(status_msg)}</div>
  </div>
  <div class='detail-actions'>
    <button class='pill-btn' onclick="{_action_js('requests_update', {'request_id': selected.get('request_id'), 'status': 'in_progress'})}">Mark In Progress</button>
    <button class='pill-btn' onclick="{_action_js('requests_update', {'request_id': selected.get('request_id'), 'status': 'done'})}">Mark Done</button>
    <input id='request_forward_doctor_id' class='handover-forward-input' type='text' value='{html.escape(forward_doctor_id)}' placeholder='Doctor staff ID (e.g., D-01987)' />
    <button class='pill-btn' onclick="{forward_doctor_js}">Forward to Doctor</button>
  </div>
  <div class='request-assessment-status {'show' if forward_status_msg else ''}'>{html.escape(forward_status_msg)}</div>
  <div class='detail-actions detail-actions-end'>
    <button class='delete-btn-themed' onclick="if(!confirm('Delete this request?')) return false; {_action_js('requests_delete', {'request_id': selected.get('request_id')})}">Delete</button>
  </div>
</div>
"""

    return f"""
<div>
  {_page_header('Nurse Inbox', 'Triage incoming requests and coordinate patient follow-up.')}
  <div class='staff-topbar'>
    <div class='toolbar-item'>{html.escape(data.get('ward_label','Ward A'))}</div>
    <div class='toolbar-search'>
      <input type='text' placeholder='Search requests' value='{html.escape(search)}'
        onkeydown="if(event.key==='Enter'){{var val=this.value; {search_js}}}" />
    </div>
  </div>
  <div class='requests-layout'>
    <div class='card'>
      <div class='card-title'>Requests List</div>
      <div class='range-tabs request-status-tabs'>{tabs}</div>
      <div class='range-tabs source-tabs'>{source_tabs}</div>
      <div class='request-list'>{list_html}</div>
    </div>
    <div class='card'>
      {detail_html}
    </div>
  </div>
</div>
"""


def _render_settings(state: dict, ctx: dict) -> str:
    staff_id = state.get("staff_id") or ""
    ward = state.get("ward_id") or "Ward A"
    display_name = state.get("staff_display_name") or staff_id
    avatar_src = state.get("staff_avatar_data") or ctx["get_nurse_sidebar_data"](state).get("avatar", "")
    save_js = html.escape(
        "(function(){"
        "var nameEl=document.getElementById('staff_name');"
        "var file=document.getElementById('staff_avatar_file');"
        "var page=(window._wl_page||(function(){try{return localStorage.getItem('wl_page')||'';}catch(e){return '';}})());"
        "var payload={display_name:nameEl?nameEl.value:'', current_page:page};"
        "var send=function(p){wlApi('staff_settings_save', p);};"
        "if(file&&file.files&&file.files[0]){"
        "var reader=new FileReader();"
        "reader.onload=function(e){payload.avatar_data=e.target.result||'';send(payload);};"
        "reader.readAsDataURL(file.files[0]);"
        "}else{send(payload);}"
        "})(); return false;",
        quote=True,
    )
    avatar_onchange_js = html.escape(
        "var file=this.files&&this.files[0];"
        "if(!file) return;"
        "var reader=new FileReader();"
        "reader.onload=function(e){"
        "var src=e.target.result||'';"
        "var img=document.getElementById('staff_avatar_preview_img'); if(img){img.src=src;}"
        "var nav=document.querySelector('.profile img'); if(nav){nav.src=src;}"
        "};"
        "reader.readAsDataURL(file);",
        quote=True,
    )
    pass_js = _action_js("staff_settings_pass", {"old": "__OLD__", "new": "__NEW__", "confirm": "__CONF__"})
    pass_js = pass_js.replace("__OLD__", "'+oldp+'").replace("__NEW__", "'+newp+'").replace("__CONF__", "'+conf+'")
    return f"""
<div>
  {_page_header('Settings', 'Manage your account and preferences')}
  <div class='settings-grid'>
    <div class='settings-card'>
      <h4>Account</h4>
      <div class='settings-field'>
        <label>Staff ID</label>
        <input type='text' value='{html.escape(staff_id)}' readonly />
      </div>
      <div class='settings-field'>
        <label>Ward</label>
        <input type='text' value='{html.escape(ward)}' readonly />
      </div>
      <div class='settings-field'>
        <label>Display name</label>
        <input id='staff_name' type='text' value='{html.escape(display_name)}' />
      </div>
      <div class='settings-field'>
        <label>Avatar</label>
        <div class='avatar-upload'>
          <div class='avatar-preview'><img id='staff_avatar_preview_img' src='{avatar_src}' /></div>
          <div class='avatar-input'>
            <label class='upload-btn' for='staff_avatar_file'>Upload avatar</label>
            <input id='staff_avatar_file' class='avatar-file' type='file' accept='image/*'
              onchange="{avatar_onchange_js}" />
          </div>
        </div>
      </div>
      <div class='settings-field'>
        <button class='settings-save' onclick="{save_js}">Save</button>
      </div>
    </div>
    <div class='settings-card'>
      <h4>Security</h4>
      <div class='settings-field'>
        <label>Current password</label>
        <input id='staff_old_pass' type='password' placeholder='Current password' />
      </div>
      <div class='settings-field'>
        <label>New password</label>
        <input id='staff_new_pass' type='password' placeholder='New password' />
      </div>
      <div class='settings-field'>
        <label>Confirm new password</label>
        <input id='staff_confirm_pass' type='password' placeholder='Confirm new password' />
      </div>
      <div class='settings-field'>
        <button class='settings-save' onclick="var o=document.getElementById('staff_old_pass'); var n=document.getElementById('staff_new_pass'); var c=document.getElementById('staff_confirm_pass'); var oldp=o?o.value:''; var newp=n?n.value:''; var conf=c?c.value:''; {pass_js}">Update password</button>
        <div class='settings-hint'>Use at least 8 characters and avoid reusing your old password.</div>
      </div>
    </div>
  </div>
</div>
"""


def render_nurse_page(state: dict, ctx: dict) -> str:
    current_page = state.get("current_page") or "ward_dashboard"
    sidebar = _render_sidebar(state, ctx, current_page)
    sections = [
        ("ward_dashboard", _render_ward_dashboard(state, ctx)),
        ("vitals_mar", _render_vitals_mar(state, ctx)),
        ("generate_assessment", _render_assessment(state, ctx)),
        ("handover_summary", _render_handover(state, ctx)),
        ("nurse_inbox", _render_inbox(state, ctx)),
        ("settings", _render_settings(state, ctx)),
    ]
    main_sections = ""
    for page, content in sections:
        style = "display:block;" if page == current_page else "display:none;"
        main_sections += f"<div class='page-section' data-page='{page}' style='{style}'>{content}</div>"
    return f"""
<div class="dash-page">
{sidebar}
  <div class="main">
    {main_sections}
  </div>
</div>
"""


def _render_doctor_sidebar(state: dict, ctx: dict, current_page: str) -> str:
    icons = ctx.get("icons", {})
    sidebar_data = ctx["get_doctor_sidebar_data"](state)
    nav_items = [
        (icons.get("dashboard", ""), "Doctor Dashboard", "doctor_dashboard"),
        (icons.get("card", ""), "Patient 360", "doctor_patient_360"),
        (icons.get("calendar", ""), "Orders & Plan", "doctor_orders_plan"),
        (icons.get("chat", ""), "Doctor Inbox", "doctor_inbox"),
        (icons.get("settings", ""), "Settings", "doctor_settings"),
    ]
    nav_html = "".join(_nav_item(icon, label, page, current_page) for icon, label, page in nav_items)
    logout_builder = ctx.get("onclick")
    logout_onclick = logout_builder("do_logout") if callable(logout_builder) else ""
    return f"""
  <div class="sidebar">
    <div class="brand">
      <img src="{ctx.get('logo_data','')}" />
      <div class="brand-text">WardLung <span class="compass">Compass</span></div>
    </div>
    <div class="nav">{nav_html}</div>
    <div class="profile">
      <img src="{sidebar_data.get('avatar','')}" />
      <div>
        <div class="name">{html.escape(sidebar_data.get('display_name') or sidebar_data.get('staff_id','Doctor'))}</div>
        <div class="role">{html.escape(sidebar_data.get('role','Doctor'))}</div>
      </div>
    </div>
    <div class="logout" onclick="{logout_onclick}">{icons.get('logout','')} Log out</div>
  </div>
"""


def _doctor_ward_select_html(ward_picker: dict, page: str) -> str:
    options_html = "".join(
        f"<option value='{html.escape(str(o.get('value') or ''))}' {'selected' if str(o.get('value') or '')==str(ward_picker.get('selected') or '') else ''}>{html.escape(str(o.get('label') or ''))}</option>"
        for o in (ward_picker.get("options") or [])
    )
    return (
        "<div class='patient-picker'>"
        f"<select onchange=\"wlApi('doctor_update', {{ward_id:this.value, current_page:'{page}'}});\">"
        f"{options_html}"
        "</select>"
        "</div>"
    )


def _render_doctor_toolbar(data: dict) -> str:
    ward_picker = data.get("ward_picker", {})
    search = data.get("search", "")
    filter_tag = data.get("filter", "All")
    search_send_js = _action_js("doctor_update", {"search": "__VALUE__"}).replace("__VALUE__", "'+val+'")
    filter_buttons = ""
    for t in ["All", "Stable", "Needs Attention"]:
        active = "active" if t == filter_tag else ""
        filter_buttons += f"<button class='chip {active}' onclick=\"{_action_js('doctor_update', {'filter': t})}\">{t}</button>"
    return f"""
  <div class='staff-toolbar'>
    {_doctor_ward_select_html(ward_picker, 'doctor_dashboard')}
    <div class='toolbar-search'>
      <input type='text' placeholder='Search Bed / Patient ID' value='{html.escape(search)}'
        onkeydown="if(event.key==='Enter'){{var val=this.value; {search_send_js}}}" />
    </div>
    <div class='toolbar-filters'>{filter_buttons}</div>
    <button class='toolbar-refresh' onclick="{_action_js('doctor_update', {'refresh': True})}">Refresh</button>
  </div>
"""


def _render_doctor_dashboard(state: dict, ctx: dict) -> str:
    data = ctx["get_doctor_dashboard_data"](state)
    rows = ""
    for p in data.get("patients", []):
        risk_class = f"risk-{p.get('risk_level','stable')}"
        open_js = html.escape(
            "(function(){"
            "try{localStorage.setItem('wl_page','doctor_patient_360');}catch(e){}"
            "window._wl_page='doctor_patient_360';"
            f"wlApi('doctor_select_patient', {{patient_id:{json.dumps(p.get('patient_id'))}, current_page:'doctor_patient_360'}});"
            "})(); return false;",
            quote=True,
        )
        rows += f"""
<div class='ward-row'>
  <div>{html.escape(str(p.get('bed_id','--')))}</div>
  <div class='mono'>{html.escape(str(p.get('patient_id','')))}</div>
  <div><span class='risk-badge {risk_class}'>{html.escape(p.get('risk_label','Stable'))}</span></div>
  <div>{html.escape(str(p.get('last_vitals','--')))}</div>
  <div>{html.escape(str(p.get('last_assessment','Pending')))}</div>
  <div>{html.escape(str(p.get('last_handover','None')))}</div>
  <div><button class='pill-btn' onclick="{open_js}">Open</button></div>
</div>
"""
    if not rows:
        rows = "<div class='ward-row empty'>No patients found.</div>"

    source = data.get("source_count", {})
    return f"""
<div class='ward-dashboard-page'>
  {_page_header('Doctor Dashboard', 'Focus on decision-level triage, risk review, and clinical follow-up.')} 
  {_render_doctor_toolbar(data)}
  <div class='ward-grid'>
    <div class='ward-table card'>
      <div class='card-title'>Patients in Ward</div>
      <div class='ward-head'>
        <div>Bed</div><div>Patient ID</div><div>Risk</div><div>Last Vitals</div>
        <div>Last Assessment</div><div>Last Handover</div><div>Action</div>
      </div>
      {rows}
    </div>
    <div class='split-cards'>
      <div class='card'>
        <div class='card-title'>Review Queue</div>
        <div class='summary-text'>Pending: {int(data.get('pending_count', 0))}</div>
        <div class='summary-text'>In progress: {int(data.get('in_progress_count', 0))}</div>
        <div class='summary-text'>Done: {int(data.get('done_count', 0))}</div>
      </div>
      <div class='card'>
        <div class='card-title'>Pending Sources</div>
        <div class='summary-text'>From patient: {int(source.get('Patient', 0))}</div>
        <div class='summary-text'>From nurse: {int(source.get('Nurse', 0))}</div>
        <div class='summary-text'>From doctor: {int(source.get('Doctor', 0))}</div>
      </div>
    </div>
  </div>
</div>
"""


def _format_doctor_gap(gap: object) -> str:
    if isinstance(gap, dict):
        severity = str(gap.get("severity") or "").strip().upper()
        message = _to_english_gap(str(gap.get("message") or gap.get("id") or "Missing clinical input."))
        suggested = gap.get("suggested_fields") if isinstance(gap.get("suggested_fields"), list) else []
        suggested_text = ", ".join(str(x).strip() for x in suggested if str(x).strip())
        if suggested_text:
            message = f"{message} (Suggested fields: {suggested_text})"
        if severity:
            return f"[{severity}] {message}"
        return message
    return _to_english_gap(str(gap or "Missing clinical input."))


def _format_doctor_evidence(item: object) -> str:
    if isinstance(item, dict):
        source = str(item.get("source_file") or item.get("source_path") or item.get("category") or "source").strip()
        score = item.get("score")
        text = str(item.get("text") or "").strip()
        header = f"[{source}]" if source else "[source]"
        if isinstance(score, (int, float)):
            header += f" (score {float(score):.2f})"
        return f"{header} {text}".strip()
    return str(item or "").strip()


def _format_doctor_trace(item: object) -> str:
    if isinstance(item, dict):
        step = str(item.get("step") or "pipeline").strip()
        status = str(item.get("status") or "").strip()
        summary = str(item.get("summary") or "").strip()
        latency = item.get("latency_ms")
        parts = [step]
        if status:
            parts.append(status)
        if isinstance(latency, (int, float)):
            parts.append(f"{int(latency)}ms")
        header = " / ".join(parts)
        return f"{header}: {summary}" if summary else header
    return str(item or "").strip()


def _render_doctor_patient360(state: dict, ctx: dict) -> str:
    data = ctx["get_doctor_patient360_data"](state)
    ward_picker = data.get("ward_picker", {})
    picker = data.get("picker", {})
    patient = data.get("patient") or {}
    risk = data.get("risk") or {}
    assessment = data.get("assessment") or {}
    current = data.get("current") or {}
    note_text = str(data.get("note_text") or "")
    note_status_msg = str(data.get("note_status_msg") or "")
    assessment_note_text = str(data.get("assessment_note_text") or "")
    assessment_status_msg = str(data.get("assessment_status_msg") or "")

    options_html = "".join(
        f"<option value='{o['value']}' {'selected' if o['value']==picker.get('selected') else ''}>{html.escape(o['label'])}</option>"
        for o in picker.get("options", [])
    )
    selected_patient_json = json.dumps(picker.get("selected"))
    note_save_js = html.escape(
        "(function(){"
        "var ta=document.getElementById('doctor_note_text');"
        "var txt=ta?ta.value:'';"
        f"wlApi('doctor_note_save', {{patient_id:{selected_patient_json}, text:txt, current_page:'doctor_patient_360'}});"
        "})(); return false;",
        quote=True,
    )
    note_send_js = html.escape(
        "(function(){"
        "var ta=document.getElementById('doctor_note_text');"
        "var txt=ta?ta.value:'';"
        f"wlApi('doctor_note_send', {{patient_id:{selected_patient_json}, text:txt, current_page:'doctor_patient_360'}});"
        "})(); return false;",
        quote=True,
    )
    assessment_generate_js = html.escape(
        "(async function(){"
        "var ta=document.getElementById('doctor_assessment_note');"
        "var txt=ta?ta.value:'';"
        "var btn=document.getElementById('doctor_assessment_generate_btn');"
        "var status=document.getElementById('doctor_assessment_status');"
        "if(btn){btn.disabled=true;btn.textContent='Generating...';}"
        "if(status){status.classList.add('show');status.textContent='Generating assessment...';}"
        "try{"
        f"await wlApi('doctor_assessment_generate', {{patient_id:{selected_patient_json}, note:txt, current_page:'doctor_patient_360'}});"
        "}finally{"
        "if(btn){btn.disabled=false;btn.textContent='Generate / Refresh Assessment';}"
        "}"
        "})(); return false;",
        quote=True,
    )
    risk_class = f"risk-{risk.get('level','stable')}"
    daily_html = "".join(
        f"<li><b>{html.escape(str(x.get('time','')))}</b> - {html.escape(str(x.get('text','')))}</li>"
        for x in (data.get("timeline_daily") or [])
    ) or "<li>No recent daily checks.</li>"
    admin_html = "".join(
        f"<li><b>{html.escape(str(x.get('time','')))}</b> - {html.escape(str(x.get('text','')))}</li>"
        for x in (data.get("timeline_admin") or [])
    ) or "<li>No recent vitals/MAR records.</li>"
    chat_html = "".join(
        f"<li><b>{html.escape(str(x.get('time','')))}</b> - {html.escape(str(x.get('text','')))}</li>"
        for x in (data.get("timeline_chat") or [])
    ) or "<li>No recent chat summary.</li>"
    gaps_html = "".join(
        f"<li>{html.escape(_format_doctor_gap(g))}</li>"
        for g in (assessment.get("gaps") or [])[:5]
    ) or "<li>No major gaps listed.</li>"
    actions_html = "".join(
        f"<li>{html.escape(str(a))}</li>" for a in (risk.get("next_actions") or [])[:5]
    ) or "<li>No immediate actions listed.</li>"
    flags_html = "".join(
        f"<li>{html.escape(str(f))}</li>" for f in (risk.get("flags") or [])[:5]
    ) or "<li>No active risk flags.</li>"
    audit = assessment.get("audit") if isinstance(assessment.get("audit"), dict) else {}
    reverse = assessment.get("reverse") if isinstance(assessment.get("reverse"), dict) else {}
    audit_error = str(audit.get("error") or "").strip()
    reverse_error = str(reverse.get("error") or "").strip()
    audit_critique_html = "".join(
        f"<li>{html.escape(str(item))}</li>"
        for item in (audit.get("critique") if isinstance(audit.get("critique"), list) else [])[:5]
        if str(item).strip()
    ) or "<li>No critique provided.</li>"
    alternatives_html = "".join(
        f"<li>{html.escape(str(item))}</li>"
        for item in (reverse.get("alternative_diagnoses") if isinstance(reverse.get("alternative_diagnoses"), list) else [])[:5]
        if str(item).strip()
    ) or "<li>No alternative diagnosis listed.</li>"
    rule_out_rows = reverse.get("rule_out_logic") if isinstance(reverse.get("rule_out_logic"), list) else []
    rule_out_html = ""
    for row in rule_out_rows[:5]:
        if isinstance(row, dict):
            suspect = str(row.get("suspect") or "").strip()
            why = str(row.get("why_dangerous") or "").strip()
            action = str(row.get("action_to_exclude") or "").strip()
            parts = []
            if suspect:
                parts.append(f"{suspect}.")
            if why:
                parts.append(f"Why dangerous: {why}")
            if action:
                parts.append(f"Rule-out action: {action}")
            line = " ".join(parts).strip()
            if line:
                rule_out_html += f"<li>{html.escape(line)}</li>"
        else:
            text = str(row).strip()
            if text:
                rule_out_html += f"<li>{html.escape(text)}</li>"
    if not rule_out_html:
        rule_out_html = "<li>No rule-out actions listed.</li>"
    evidence_html = "".join(
        f"<li>{html.escape(_format_doctor_evidence(item))}</li>"
        for item in (assessment.get("evidence") or [])[:3]
        if _format_doctor_evidence(item)
    ) or "<li>No RAG evidence preview.</li>"
    trace_html = "".join(
        f"<li>{html.escape(_format_doctor_trace(item))}</li>"
        for item in (assessment.get("tool_trace") or [])[:5]
        if _format_doctor_trace(item)
    ) or "<li>No tool trace preview.</li>"
    reverse_error_html = (
        f"<div class='summary-text'>{html.escape(f'Reverse generation error: {reverse_error}')}</div>" if reverse_error else ""
    )

    return f"""
<div class='doctor-patient-page'>
  {_page_header('Patient 360', 'Review full patient context before making doctor-level decisions.')}
  <div class='staff-topbar'>
    {_doctor_ward_select_html(ward_picker, 'doctor_patient_360')}
    <div class='patient-picker'>
      <select onchange="wlApi('doctor_select_patient', {{patient_id:this.value, current_page:'doctor_patient_360'}});">
        {options_html}
      </select>
    </div>
    <div class='staff-meta'>Doctor {html.escape(state.get('staff_id') or '')}</div>
  </div>
  <div class='split-cards'>
    <div class='card'>
      <div class='card-title'>Patient Profile</div>
      <div class='summary-text'>Patient: {html.escape(str(patient.get('patient_id','--')))}</div>
      <div class='summary-text'>Bed: {html.escape(str(patient.get('bed_id','--')))}</div>
      <div class='summary-text'>Age/Sex: {html.escape(str(patient.get('age','--')))} / {html.escape(str(patient.get('sex','--')))}</div>
      <div class='summary-text'>Allergy history: {html.escape(str(patient.get('allergy_history') or '--'))}</div>
      <div class='summary-text'>Current vitals: {html.escape(str(current.get('vitals_text','--')))}</div>
      <div class='summary-text'>Current MAR: {html.escape(str(current.get('mar_text','--')))}</div>
    </div>
    <div class='card'>
      <div class='card-title'>Risk & Watch</div>
      <div><span class='risk-badge {risk_class}'>{html.escape(str(risk.get('label','Stable')))}</span></div>
      <div class='summary-text'>Risk score: {html.escape(str(risk.get('score', 0)))}</div>
      <div class='sub-title'>Risk flags</div>
      <ul class='gap-list'>{flags_html}</ul>
      <div class='sub-title'>Recommended next actions</div>
      <ul class='gap-list'>{actions_html}</ul>
    </div>
  </div>
  <div class='split-cards doctor-patient360-stack'>
    <div class='card'>
      <div class='card-title'>Clinical Timeline</div>
      <div class='sub-title'>Daily checks (recent)</div>
      <ul class='gap-list'>{daily_html}</ul>
      <div class='sub-title'>Vitals & MAR (recent)</div>
      <ul class='gap-list'>{admin_html}</ul>
      <div class='sub-title'>Chat summaries (recent)</div>
      <ul class='gap-list'>{chat_html}</ul>
    </div>
    <div class='card'>
      <div class='card-title'>Assessment Review</div>
      <div class='auto-summary'>Generate a fresh assessment from current timeline and nursing records.</div>
      <textarea id='doctor_assessment_note' class='request-assessment-textarea' rows='5' placeholder='Optional doctor notes for this reassessment...'>{html.escape(assessment_note_text)}</textarea>
      <div class='detail-actions'>
        <button id='doctor_assessment_generate_btn' class='generate-btn' onclick="{assessment_generate_js}">Generate / Refresh Assessment</button>
      </div>
      <div id='doctor_assessment_status' class='request-assessment-status {'show' if assessment_status_msg else ''}'>{html.escape(assessment_status_msg)}</div>
      <div class='summary-text'>Last assessment time: {html.escape(str(assessment.get('timestamp') or 'Not available'))}</div>
      <div class='summary-text'>Primary diagnosis: {html.escape(str(assessment.get('primary_diagnosis','Not specified')))}</div>
      <div class='summary-text'>Risk level: {html.escape(str(assessment.get('risk_level','Not specified')))}</div>
      <div class='summary-text'>Confidence: {html.escape(str(assessment.get('confidence_score') if assessment.get('confidence_score') is not None else 'Not provided'))}</div>
      <div class='summary-text'>{html.escape(str(assessment.get('summary') or ''))}</div>
      <div class='sub-title'>Gaps</div>
      <ul class='gap-list'>{gaps_html}</ul>
      <div class='sub-title'>Audit review</div>
      <div class='summary-text'>Status: {html.escape(str(audit.get('audit_status') or ('Unavailable' if audit_error else 'Not provided')))}</div>
      <div class='summary-text'>Audit risk: {html.escape(str(audit.get('audit_risk_score') or ('Unavailable' if audit_error else 'Not provided')))}</div>
      <div class='summary-text'>Safety warning: {html.escape(str(audit.get('safety_warning') or ('Audit error: ' + audit_error if audit_error else 'None')))}</div>
      <div class='sub-title'>Audit critique</div>
      <ul class='gap-list'>{audit_critique_html}</ul>
      <div class='sub-title'>Differential diagnosis</div>
      <ul class='gap-list'>{alternatives_html}</ul>
      <div class='sub-title'>Rule-out actions</div>
      <ul class='gap-list'>{rule_out_html}</ul>
      <div class='sub-title'>Evidence preview</div>
      <ul class='gap-list'>{evidence_html}</ul>
      <div class='sub-title'>Pipeline trace</div>
      <ul class='gap-list'>{trace_html}</ul>
      {reverse_error_html}
    </div>
  </div>
  <div class='card'>
    <div class='card-title'>Doctor Message to Patient</div>
    <div class='auto-summary'>Write a concise patient-facing plan or clarification, then send to patient inbox.</div>
    <textarea id='doctor_note_text' class='request-assessment-textarea' rows='7' placeholder='Type doctor message here...'>{html.escape(note_text)}</textarea>
    <div class='detail-actions'>
      <button class='pill-btn' onclick="{note_save_js}">Save Draft</button>
      <button class='pill-btn primary' onclick="{note_send_js}">Send to Patient Inbox</button>
    </div>
    <div class='request-assessment-status {'show' if note_status_msg else ''}'>{html.escape(note_status_msg)}</div>
  </div>
</div>
"""


def _render_doctor_orders_plan(state: dict, ctx: dict) -> str:
    data = ctx["get_doctor_orders_data"](state)
    ward_picker = data.get("ward_picker", {})
    picker = data.get("picker", {})
    patient = data.get("patient") or {}
    plan_text = str(data.get("plan_text") or "")
    preview_text = str(data.get("preview_text") or "")
    status_msg = str(data.get("status_msg") or "")
    options_html = "".join(
        f"<option value='{o['value']}' {'selected' if o['value']==picker.get('selected') else ''}>{html.escape(o['label'])}</option>"
        for o in picker.get("options", [])
    )
    selected_patient_json = json.dumps(picker.get("selected"))
    preview_js = html.escape(
        "(async function(btn){"
        "if(btn){btn.disabled=true;btn.textContent='Generating...';}"
        "var plan=document.getElementById('doctor_plan_text');"
        "var txt=plan?plan.value:'';"
        "try{"
        f"await wlApi('doctor_orders_preview', {{patient_id:{selected_patient_json}, plan_text:txt, current_page:'doctor_orders_plan'}});"
        "}finally{"
        "if(btn){btn.disabled=false;btn.textContent='Generate Preview';}"
        "}"
        "})(this); return false;",
        quote=True,
    )
    save_js = html.escape(
        "(function(){"
        "var plan=document.getElementById('doctor_plan_text');"
        "var prev=document.getElementById('doctor_plan_preview_text');"
        "var ptxt=plan?plan.value:'';"
        "var vtxt=prev?prev.value:'';"
        f"wlApi('doctor_orders_save', {{patient_id:{selected_patient_json}, plan_text:ptxt, preview_text:vtxt, current_page:'doctor_orders_plan'}});"
        "})(); return false;",
        quote=True,
    )
    send_js = html.escape(
        "(function(){"
        "var plan=document.getElementById('doctor_plan_text');"
        "var prev=document.getElementById('doctor_plan_preview_text');"
        "var ptxt=plan?plan.value:'';"
        "var vtxt=prev?prev.value:'';"
        f"wlApi('doctor_orders_send', {{patient_id:{selected_patient_json}, plan_text:ptxt, preview_text:vtxt, current_page:'doctor_orders_plan'}});"
        "})(); return false;",
        quote=True,
    )
    return f"""
<div class='doctor-orders-page'>
  {_page_header('Orders & Plan', 'Edit treatment goals, checks, and a patient-friendly summary before sending.')}
  <div class='staff-topbar'>
    {_doctor_ward_select_html(ward_picker, 'doctor_orders_plan')}
    <div class='patient-picker'>
      <select onchange="wlApi('doctor_select_patient', {{patient_id:this.value, current_page:'doctor_orders_plan'}});">
        {options_html}
      </select>
    </div>
    <div class='staff-meta'>Doctor {html.escape(state.get('staff_id') or '')}</div>
  </div>
  <div class='split-cards'>
    <div class='card'>
      <div class='card-title'>Plan Editor</div>
      <div class='auto-summary'>Patient: Bed {html.escape(str(patient.get('bed_id','--')))} | {html.escape(str(patient.get('patient_id','--')))}</div>
      <textarea id='doctor_plan_text' class='request-assessment-textarea' rows='14' placeholder='Treatment goals, tests, and observation points...'>{html.escape(plan_text)}</textarea>
      <div class='detail-actions'>
        <button class='pill-btn primary' onclick="{preview_js}">Generate Preview</button>
      </div>
    </div>
    <div class='card'>
      <div class='card-title'>Patient-friendly Preview</div>
      <div class='auto-summary'>Editable before send.</div>
      <textarea id='doctor_plan_preview_text' class='request-assessment-textarea' rows='14' placeholder='Patient-facing version will appear here...'>{html.escape(preview_text)}</textarea>
      <div class='detail-actions'>
        <button class='pill-btn' onclick="{save_js}">Save Draft</button>
        <button class='pill-btn primary' onclick="{send_js}">Send to Patient Inbox</button>
      </div>
      <div class='request-assessment-status {'show' if status_msg else ''}'>{html.escape(status_msg)}</div>
    </div>
  </div>
</div>
"""


def _render_doctor_inbox(state: dict, ctx: dict) -> str:
    data = ctx["get_doctor_inbox_data"](state)
    ward_picker = data.get("ward_picker", {})
    requests = data.get("requests", [])
    selected = data.get("selected")
    filter_tab = data.get("filter", "Pending")
    source_filter = data.get("source_filter", "All")
    search = data.get("search", "")
    status_msg = str(data.get("status_msg") or "")

    tabs = ""
    for t in ["Pending", "In Progress", "Done"]:
        active = "active" if t == filter_tab else ""
        tabs += f"<button class='tab {active}' onclick=\"{_action_js('doctor_inbox_filter', {'filter': t})}\">{t}</button>"
    source_tabs = ""
    for t in ["All", "Patient", "Nurse", "Doctor"]:
        active = "active" if t == source_filter else ""
        source_tabs += f"<button class='tab {active}' onclick=\"{_action_js('doctor_inbox_source_filter', {'source_filter': t})}\">{t}</button>"
    search_js = _action_js("doctor_inbox_search", {"q": "__Q__"}).replace("__Q__", "'+val+'")

    list_html = ""
    for r in requests:
        type_badge = "<span class='tag request-type-badge'>Forwarded Handover</span>" if r.get("is_forwarded_handover") else ""
        source_badge = f"<span class='tag request-source-badge'>From {html.escape(str(r.get('source_category') or 'Patient'))}</span>"
        tags = "".join(f"<span class='tag tag-lime'>{html.escape(t)}</span>" for t in r.get("tags", [])[:2])
        list_html += f"""
<div class='request-item' onclick="{_action_js('doctor_inbox_select', {'request_id': r.get('request_id')})}">
  <div class='request-title'>Bed {html.escape(str(r.get('bed_id','')))} | {html.escape(str(r.get('patient_id','')))}</div>
  <div class='request-meta'>Created: {html.escape(str(r.get('created_at',''))[:16])}</div>
  <div class='request-summary'>{html.escape(r.get('summary',''))}</div>
  <div class='request-tags'>{source_badge}{type_badge}{tags}</div>
</div>
"""
    if not list_html:
        list_html = "<div class='empty'>No requests.</div>"

    detail_html = "<div class='empty'>Select a request</div>"
    if selected:
        selected_id_json = json.dumps(selected.get("request_id"))
        response_send_js = html.escape(
            "(function(){"
            "var ta=document.getElementById('doctor_request_reply');"
            "var txt=ta?ta.value:'';"
            f"wlApi('doctor_inbox_send', {{request_id:{selected_id_json}, text:txt, current_page:'doctor_inbox'}});"
            "})(); return false;",
            quote=True,
        )
        audio_url = str(selected.get("audio_path") or "").strip()
        audio_html = "<div class='audio-player empty'>No audio</div>"
        if audio_url:
            audio_html = (
                f"<audio class='audio-player-el' controls preload='none' src='{html.escape(audio_url, quote=True)}'></audio>"
            )
        image_items = []
        for u in selected.get("images") or []:
            url = str(u or "").strip()
            if not url:
                continue
            image_items.append(
                "<a class='thumb-link' href='{0}' target='_blank' rel='noopener noreferrer'>"
                "<img class='thumb-img' src='{0}' alt='request image' />"
                "</a>".format(html.escape(url, quote=True))
            )
        images = "".join(image_items) if image_items else "<div class='attachments-empty'>No images</div>"
        detail_html = f"""
<div class='detail-card'>
  <div class='detail-title'>Request Detail</div>
  <div class='detail-source'>Source: {html.escape(str(selected.get('source_category') or 'Patient'))}</div>
  <div class='detail-info'>Patient Info: Bed {html.escape(str(selected.get('bed_id','')))} | {html.escape(str(selected.get('patient_id','')))}</div>
  <div class='detail-status'>Status: {html.escape(str(selected.get('status','Pending')).title())}</div>
  <div class='detail-section'>
    <div class='sub-title'>Full Symptom Summary</div>
    <div>{html.escape(selected.get('detail',''))}</div>
  </div>
  <div class='detail-section'>
    <div class='sub-title'>Attachments</div>
    <div class='attachments'>
      {audio_html}
      <div class='image-grid'>{images}</div>
    </div>
  </div>
  <div class='detail-section request-assessment-panel'>
    <div class='sub-title'>Doctor Reply to Patient</div>
    <textarea id='doctor_request_reply' class='request-assessment-textarea' rows='6' placeholder='Type clinical clarification or next-step advice...'></textarea>
    <div class='detail-actions'>
      <button class='pill-btn primary' onclick="{response_send_js}">Send Reply</button>
    </div>
    <div class='request-assessment-status {'show' if status_msg else ''}'>{html.escape(status_msg)}</div>
  </div>
  <div class='detail-actions'>
    <button class='pill-btn' onclick="{_action_js('doctor_inbox_update', {'request_id': selected.get('request_id'), 'status': 'in_progress'})}">Mark In Progress</button>
    <button class='pill-btn' onclick="{_action_js('doctor_inbox_update', {'request_id': selected.get('request_id'), 'status': 'done'})}">Mark Done</button>
  </div>
  <div class='detail-actions detail-actions-end'>
    <button class='delete-btn-themed' onclick="if(!confirm('Delete this request?')) return false; {_action_js('doctor_inbox_delete', {'request_id': selected.get('request_id')})}">Delete</button>
  </div>
</div>
"""

    return f"""
<div>
  {_page_header('Doctor Inbox', 'Review escalations forwarded by nurses or raised from patient risk signals.')}
  <div class='staff-topbar'>
    {_doctor_ward_select_html(ward_picker, 'doctor_inbox')}
    <div class='toolbar-search'>
      <input type='text' placeholder='Search requests' value='{html.escape(search)}'
        onkeydown="if(event.key==='Enter'){{var val=this.value; {search_js}}}" />
    </div>
  </div>
  <div class='requests-layout'>
    <div class='card'>
      <div class='card-title'>Requests List</div>
      <div class='range-tabs request-status-tabs'>{tabs}</div>
      <div class='range-tabs source-tabs'>{source_tabs}</div>
      <div class='request-list'>{list_html}</div>
    </div>
    <div class='card'>
      {detail_html}
    </div>
  </div>
</div>
"""


def _render_doctor_settings(state: dict, ctx: dict) -> str:
    staff_id = state.get("staff_id") or ""
    ward = state.get("ward_id") or "ward_a"
    display_name = state.get("staff_display_name") or staff_id
    avatar_src = state.get("staff_avatar_data") or ctx["get_doctor_sidebar_data"](state).get("avatar", "")
    patient_status_msg = str(state.get("doctor_create_patient_status_msg") or "")
    nurse_status_msg = str(state.get("doctor_create_nurse_status_msg") or "")

    save_js = html.escape(
        "(function(){"
        "var nameEl=document.getElementById('doctor_staff_name');"
        "var file=document.getElementById('doctor_staff_avatar_file');"
        "var payload={display_name:nameEl?nameEl.value:'', current_page:'doctor_settings'};"
        "var send=function(p){wlApi('doctor_settings_save', p);};"
        "if(file&&file.files&&file.files[0]){"
        "var reader=new FileReader();"
        "reader.onload=function(e){payload.avatar_data=e.target.result||'';send(payload);};"
        "reader.readAsDataURL(file.files[0]);"
        "}else{send(payload);} "
        "})(); return false;",
        quote=True,
    )
    avatar_onchange_js = html.escape(
        "var file=this.files&&this.files[0];"
        "if(!file) return;"
        "var reader=new FileReader();"
        "reader.onload=function(e){"
        "var src=e.target.result||'';"
        "var img=document.getElementById('doctor_staff_avatar_preview_img'); if(img){img.src=src;}"
        "var nav=document.querySelector('.profile img'); if(nav){nav.src=src;}"
        "};"
        "reader.readAsDataURL(file);",
        quote=True,
    )
    pass_js = _action_js("doctor_settings_pass", {"old": "__OLD__", "new": "__NEW__", "confirm": "__CONF__"})
    pass_js = pass_js.replace("__OLD__", "'+oldp+'").replace("__NEW__", "'+newp+'").replace("__CONF__", "'+conf+'")
    create_patient_js = html.escape(
        "(function(){"
        "var pid=document.getElementById('doc_new_patient_id');"
        "var ward=document.getElementById('doc_new_patient_ward');"
        "var bed=document.getElementById('doc_new_patient_bed');"
        "var sex=document.getElementById('doc_new_patient_sex');"
        "var age=document.getElementById('doc_new_patient_age');"
        "var allergy=document.getElementById('doc_new_patient_allergy');"
        "wlApi('doctor_create_patient', {"
        "patient_id:pid?pid.value:'',"
        "ward_id:ward?ward.value:'',"
        "bed_id:bed?bed.value:'',"
        "sex:sex?sex.value:'',"
        "age:age?age.value:'',"
        "allergy_history:allergy?allergy.value:'',"
        "current_page:'doctor_settings'"
        "});"
        "})(); return false;",
        quote=True,
    )
    create_nurse_js = html.escape(
        "(function(){"
        "var sid=document.getElementById('doc_new_nurse_id');"
        "var ward=document.getElementById('doc_new_nurse_ward');"
        "var name=document.getElementById('doc_new_nurse_name');"
        "var email=document.getElementById('doc_new_nurse_email');"
        "wlApi('doctor_create_nurse', {"
        "staff_id:sid?sid.value:'',"
        "ward_id:ward?ward.value:'',"
        "name:name?name.value:'',"
        "email:email?email.value:'',"
        "current_page:'doctor_settings'"
        "});"
        "})(); return false;",
        quote=True,
    )

    return f"""
<div>
  {_page_header('Settings', 'Manage doctor profile and create initial patient/nurse accounts.')}
  <div class='settings-grid'>
    <div class='settings-card'>
      <h4>Account</h4>
      <div class='settings-field'>
        <label>Doctor ID</label>
        <input type='text' value='{html.escape(staff_id)}' readonly />
      </div>
      <div class='settings-field'>
        <label>Ward</label>
        <input type='text' value='{html.escape(ward)}' readonly />
      </div>
      <div class='settings-field'>
        <label>Display name</label>
        <input id='doctor_staff_name' type='text' value='{html.escape(display_name)}' />
      </div>
      <div class='settings-field'>
        <label>Avatar</label>
        <div class='avatar-upload'>
          <div class='avatar-preview'><img id='doctor_staff_avatar_preview_img' src='{avatar_src}' /></div>
          <div class='avatar-input'>
            <label class='upload-btn' for='doctor_staff_avatar_file'>Upload avatar</label>
            <input id='doctor_staff_avatar_file' class='avatar-file' type='file' accept='image/*'
              onchange="{avatar_onchange_js}" />
          </div>
        </div>
      </div>
      <div class='settings-field'>
        <button class='settings-save' onclick="{save_js}">Save</button>
      </div>
    </div>
    <div class='settings-card'>
      <h4>Security</h4>
      <div class='settings-field'>
        <label>Current password</label>
        <input id='doctor_old_pass' type='password' placeholder='Current password' />
      </div>
      <div class='settings-field'>
        <label>New password</label>
        <input id='doctor_new_pass' type='password' placeholder='New password' />
      </div>
      <div class='settings-field'>
        <label>Confirm new password</label>
        <input id='doctor_confirm_pass' type='password' placeholder='Confirm new password' />
      </div>
      <div class='settings-field'>
        <button class='settings-save' onclick="var o=document.getElementById('doctor_old_pass'); var n=document.getElementById('doctor_new_pass'); var c=document.getElementById('doctor_confirm_pass'); var oldp=o?o.value:''; var newp=n?n.value:''; var conf=c?c.value:''; {pass_js}">Update password</button>
        <div class='settings-hint'>Use at least 8 characters and avoid reusing your old password.</div>
      </div>
    </div>
  </div>
  <div class='settings-grid'>
    <div class='settings-card'>
      <h4>Create Patient Account</h4>
      <div class='settings-field'><label>Patient ID</label><input id='doc_new_patient_id' type='text' placeholder='e.g., P20260214-0005' /></div>
      <div class='settings-field'><label>Ward</label><input id='doc_new_patient_ward' type='text' value='' placeholder='e.g., ward_a' /></div>
      <div class='settings-field'><label>Bed</label><input id='doc_new_patient_bed' type='text' placeholder='e.g., A-05' /></div>
      <div class='settings-field'><label>Sex</label><input id='doc_new_patient_sex' type='text' placeholder='Male / Female / Other' /></div>
      <div class='settings-field'><label>Age</label><input id='doc_new_patient_age' type='number' min='0' placeholder='e.g., 72' /></div>
      <div class='settings-field'><label>Allergy history</label><input id='doc_new_patient_allergy' type='text' placeholder='e.g., Penicillin' /></div>
      <div class='settings-field'>
        <button class='settings-save' onclick="{create_patient_js}">Create / Update Patient</button>
        <div class='settings-hint'>Default password for new patient accounts: Demo@123</div>
        <div class='request-assessment-status {'show' if patient_status_msg else ''}'>{html.escape(patient_status_msg)}</div>
      </div>
    </div>
    <div class='settings-card'>
      <h4>Create Nurse Account</h4>
      <div class='settings-field'><label>Nurse Staff ID</label><input id='doc_new_nurse_id' type='text' placeholder='e.g., N-08321' /></div>
      <div class='settings-field'><label>Ward</label><input id='doc_new_nurse_ward' type='text' value='' placeholder='e.g., ward_a' /></div>
      <div class='settings-field'><label>Name</label><input id='doc_new_nurse_name' type='text' placeholder='e.g., Nurse Lee' /></div>
      <div class='settings-field'><label>Email (optional)</label><input id='doc_new_nurse_email' type='text' placeholder='e.g., nurse_lee@wardlung.org' /></div>
      <div class='settings-field'>
        <button class='settings-save' onclick="{create_nurse_js}">Create / Update Nurse</button>
        <div class='settings-hint'>Default password for new nurse accounts: Demo@123</div>
        <div class='request-assessment-status {'show' if nurse_status_msg else ''}'>{html.escape(nurse_status_msg)}</div>
      </div>
    </div>
  </div>
</div>
"""


def render_doctor_page(state: dict, ctx: dict) -> str:
    current_page = state.get("current_page") or "doctor_dashboard"
    sidebar = _render_doctor_sidebar(state, ctx, current_page)
    sections = [
        ("doctor_dashboard", _render_doctor_dashboard(state, ctx)),
        ("doctor_patient_360", _render_doctor_patient360(state, ctx)),
        ("doctor_orders_plan", _render_doctor_orders_plan(state, ctx)),
        ("doctor_inbox", _render_doctor_inbox(state, ctx)),
        ("doctor_settings", _render_doctor_settings(state, ctx)),
    ]
    main_sections = ""
    for page, content in sections:
        style = "display:block;" if page == current_page else "display:none;"
        main_sections += f"<div class='page-section' data-page='{page}' style='{style}'>{content}</div>"
    return f"""
<div class="dash-page">
{sidebar}
  <div class="main">
    {main_sections}
  </div>
</div>
"""
