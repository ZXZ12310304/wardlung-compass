from typing import Any, Dict, List, Optional
import html as _html


def _esc(x: Any) -> str:
    
    return _html.escape("" if x is None else str(x))


def _fmt_list(xs: Any) -> str:
    if not xs:
        return "None"
    if not isinstance(xs, list):
        return _esc(xs)
    return ", ".join(_esc(x) for x in xs) if xs else "None"


def render_run_meta_panel(meta: Optional[Dict[str, Any]]) -> str:
    """
    result["meta"]：
      - route_tag: audio_only / image_only / audio_image / none
      - primary_basis: audio|image|rag|clinical|mixed
      - has_audio/has_image
      - audio_quality_score/image_quality_score
      - audio_issues/image_issues
      - rag_used
    """
    if not meta:
        return ""

    route_tag = _esc(meta.get("route_tag", "unknown"))
    basis = _esc(meta.get("primary_basis", "unknown"))
    rag_used = bool(meta.get("rag_used", False))

    has_audio = bool(meta.get("has_audio", False))
    has_image = bool(meta.get("has_image", False))

    audio_score = meta.get("audio_quality_score", None)
    image_score = meta.get("image_quality_score", None)

    audio_issues = meta.get("audio_issues", []) or []
    image_issues = meta.get("image_issues", []) or []

    def _card(title: str, enabled: bool, score: Any, issues: Any) -> str:
        enabled_txt = "Yes" if enabled else "No"
        score_txt = "N/A" if score is None else _esc(score)
        issues_txt = _fmt_list(issues)
        return f"""
        <div style="flex:1;min-width:260px;border:1px solid #e5e7eb;border-radius:14px;padding:12px;background:#ffffff;">
          <div style="font-weight:700;color:#111827;margin-bottom:6px;">{_esc(title)}</div>
          <div style="font-size:13px;color:#374151;line-height:1.7;">
            <div>enabled: <b>{enabled_txt}</b></div>
            <div>quality_score: <b>{score_txt}</b></div>
            <div>issues: <b>{issues_txt}</b></div>
          </div>
        </div>
        """

    badge = f"""
      <span style="display:inline-block;padding:4px 10px;border-radius:999px;
                   background:#f3f4f6;color:#111827;font-size:12px;margin-right:8px;">
        route: <b>{route_tag}</b>
      </span>
      <span style="display:inline-block;padding:4px 10px;border-radius:999px;
                   background:#eef2ff;color:#1e3a8a;font-size:12px;margin-right:8px;">
        basis: <b>{basis}</b>
      </span>
      <span style="display:inline-block;padding:4px 10px;border-radius:999px;
                   background:#ecfeff;color:#155e75;font-size:12px;">
        rag_used: <b>{_esc(rag_used)}</b>
      </span>
    """

    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:16px;padding:14px;background:#fafafa;margin-bottom:12px;">
      <div style="margin-bottom:10px;">{badge}</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;">
        {_card("Audio", has_audio, audio_score, audio_issues)}
        {_card("Image", has_image, image_score, image_issues)}
      </div>
    </div>
    """


def render_doctor_view_advanced(
    diagnosis: Dict[str, Any],
    audit: Dict[str, Any],
    reverse: Dict[str, Any],
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    
    # ---- 0) Meta Panel (Optional) ----
    meta_html = render_run_meta_panel(meta)

    # ---- 1) Audit Block ----
    risk = audit.get("audit_risk_score", "N/A")
    if risk == "Low":
        audit_color = "#16a34a"   # green
    elif risk == "Medium":
        audit_color = "#f59e0b"   # amber
    else:
        audit_color = "#dc2626"   # red

    critique_items = "".join([f"<li>{_esc(c)}</li>" for c in (audit.get("critique", []) or [])])

    audit_html = f"""
    <div style="border: 2px solid {audit_color}; padding: 12px; border-radius: 10px; margin-bottom: 12px; background:#ffffff;">
        <h3 style="color: {audit_color}; margin: 0 0 6px 0;">Senior Auditor Review</h3>
        <p style="margin: 0 0 6px 0;">
          <b>Risk Score:</b> {_esc(risk)} | <b>Status:</b> {_esc(audit.get('audit_status', 'N/A'))}
        </p>
        <ul style="margin: 0; padding-left: 20px;">{critique_items}</ul>
    </div>
    """

    # ---- 2) Main Report ----
    if audit.get("audit_status") == "Fail":
        main_report_html = """
        <div style="border:1px solid #fecaca;background:#fff1f2;padding:12px;border-radius:10px;margin-bottom:12px;">
          <h3 style="margin:0 0 6px 0;color:#b91c1c;">Report Blocked by Auditor due to Safety Risks</h3>
          <div style="color:#7f1d1d;">Please review the Audit Log above.</div>
        </div>
        """
    else:
        dx = _esc(diagnosis.get("primary_diagnosis", "Unknown"))
        conf = _esc(diagnosis.get("confidence_score", "N/A"))
        rl = _esc(diagnosis.get("risk_level", "N/A"))

        sugg = diagnosis.get("treatment_suggestions", []) or []
        redf = diagnosis.get("red_flags", []) or []

        sugg_li = "".join([f"<li>{_esc(x)}</li>" for x in sugg]) if sugg else "<li>None</li>"
        redf_li = "".join([f"<li>{_esc(x)}</li>" for x in redf]) if redf else "<li>None</li>"

        main_report_html = f"""
        <div style="border:1px solid #e5e7eb;background:#ffffff;padding:12px;border-radius:10px;margin-bottom:12px;">
          <h3 style="margin:0 0 8px 0;color:#111827;">Primary Clinical Report</h3>
          <div style="margin-bottom:10px;color:#111827;">
            <div><b>Diagnosis:</b> {dx} (Conf: {conf}%)</div>
            <div><b>Risk:</b> {rl}</div>
          </div>

          <div style="margin-bottom:10px;">
            <div style="font-weight:700;color:#111827;margin-bottom:4px;">Drivers &amp; Suggestions</div>
            <ul style="margin:0;padding-left:20px;color:#374151;">{sugg_li}</ul>
          </div>

          <div>
            <div style="font-weight:700;color:#111827;margin-bottom:4px;">Red Flags</div>
            <ul style="margin:0;padding-left:20px;color:#374151;">{redf_li}</ul>
          </div>
        </div>
        """

    # ---- 3) Reverse / Differential ----
    reverse_rows = ""
    for item in (reverse.get("rule_out_logic", []) or []):
        reverse_rows += f"""
        <tr>
            <td style="padding:8px; border-bottom:1px solid #374151; vertical-align:top; word-break:break-word;">
              <b>{_esc(item.get('suspect'))}</b>
            </td>
            <td style="padding:8px; border-bottom:1px solid #374151; vertical-align:top; word-break:break-word;">
              {_esc(item.get('action_to_exclude'))}
            </td>
        </tr>
        """

    if not reverse_rows:
        reverse_rows = """
        <tr>
          <td style="padding:8px; border-bottom:1px solid #374151;">None</td>
          <td style="padding:8px; border-bottom:1px solid #374151;">None</td>
        </tr>
        """

   
    dd_style = """
    <style>
      .dd-bridge, .dd-bridge * {
        color: #e5e7eb !important;
      }
      .dd-bridge h3 { color: #f59e0b !important; }
      .dd-bridge thead th { color: #cbd5e1 !important; }
      .dd-bridge table { width: 100%; border-collapse: collapse; }
      .dd-bridge tr:hover td { background: rgba(255,255,255,0.04) !important; }
    </style>
    """

    reverse_html = f"""
    {dd_style}
    <div class="dd-bridge" style="background-color:#111827; padding: 12px; border-radius: 10px; margin-top: 12px; border:1px solid #374151;">
        <h3 style="margin: 0 0 6px 0;">Differential Diagnosis Bridge (Rule-out)</h3>
        <p style="margin:0 0 10px 0; opacity:0.9;">
          <i>"What if it's not {_esc(diagnosis.get('primary_diagnosis'))}?"</i>
        </p>
        <table style="text-align:left;">
            <thead>
                <tr>
                  <th style="padding:8px; border-bottom:1px solid #374151;">High-Risk Alternative</th>
                  <th style="padding:8px; border-bottom:1px solid #374151;">Rule-out Action</th>
                </tr>
            </thead>
            <tbody>{reverse_rows}</tbody>
        </table>
    </div>
    """

    
    return meta_html + audit_html + main_report_html + reverse_html


def score_quiz(a1: str, a2: str, a3: str, quiz_data: List[Dict[str, Any]]) -> str:
    if not quiz_data:
        return "No quiz data."
    selected = [a1, a2, a3]
    score = 0
    msg = ""
    for i, (sel, q) in enumerate(zip(selected, quiz_data)):
        if sel:
            try:
                options = q.get("options", [])
                correct_idx = q.get("correct_index", 0)
                if isinstance(options, list) and 0 <= correct_idx < len(options):
                    correct = options[correct_idx]
                else:
                    correct = options[0] if options else "Unknown"
                is_correct = sel == correct
                score += int(is_correct)
                msg += f"**Q{i+1}:** {'OK' if is_correct else 'X'} (Ans: {correct})\n"
            except Exception as exc:
                msg += f"**Q{i+1}:** Error grading ({str(exc)})\n"
    return f"### Score: {score}/{len(quiz_data)}\n\n{msg}"


def build_patient_summary(result: Dict[str, Any]) -> str:
    summary = result.get("gentle_summary", "Error")
    next_steps = result.get("next_steps", [])
    return (
        "### Summary\n"
        f"{summary}\n\n"
        "### Next Steps\n"
        + "\n".join([f"- {x}" for x in next_steps])
    )
