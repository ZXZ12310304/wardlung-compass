# app.py
import os
import html as _html
import gradio as gr

from src.agents.observer import MedGemmaClient, MedSigLIPAnalyzer
from src.agents.asr import MedASRTranscriber
from src.agents.orchestrator import AnalysisOrchestrator
from src.tools.rag_engine import RAGEngine
from src.utils.rendering import build_patient_summary, render_doctor_view_advanced, score_quiz


# -----------------------
# Models / Engines
# -----------------------
medgemma = MedGemmaClient()
image_analyzer = MedSigLIPAnalyzer()
rag_engine = RAGEngine()
asr_transcriber = MedASRTranscriber()

orchestrator = AnalysisOrchestrator(
    medgemma,
    image_analyzer,
    rag_engine=rag_engine,
    asr_transcriber=asr_transcriber,
)


# -----------------------
# UI helper: render meta panel
# -----------------------
def _fmt_list(xs):
    if not xs:
        return "None"
    return ", ".join(_html.escape(str(x)) for x in xs)


def render_meta_panel(meta: dict) -> str:
    
    meta = meta or {}
    route_tag = _html.escape(str(meta.get("route_tag", "unknown")))
    basis = _html.escape(str(meta.get("primary_basis", "unknown")))
    rag_used = bool(meta.get("rag_used", False))

    has_audio = bool(meta.get("has_audio", False))
    has_image = bool(meta.get("has_image", False))

    audio_score = meta.get("audio_quality_score", None)
    image_score = meta.get("image_quality_score", None)

    audio_issues = meta.get("audio_issues", []) or []
    image_issues = meta.get("image_issues", []) or []

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
        rag_used: <b>{str(rag_used)}</b>
      </span>
    """

    def card(title, enabled, score, issues):
        enabled_txt = "Yes" if enabled else "No"
        score_txt = "N/A" if score is None else _html.escape(str(score))
        issues_txt = _fmt_list(issues)
        return f"""
        <div style="flex:1;min-width:260px;border:1px solid #e5e7eb;border-radius:14px;padding:12px;background:#ffffff;">
          <div style="font-weight:700;color:#111827;margin-bottom:6px;">{_html.escape(title)}</div>
          <div style="font-size:13px;color:#374151;line-height:1.6;">
            <div>enabled: <b>{enabled_txt}</b></div>
            <div>quality_score: <b>{score_txt}</b></div>
            <div>issues: <b>{issues_txt}</b></div>
          </div>
        </div>
        """

    audio_card = card("Audio", has_audio, audio_score, audio_issues)
    image_card = card("Image", has_image, image_score, image_issues)

    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:16px;padding:14px;background:#fafafa;margin-top:8px;">
      <div style="margin-bottom:10px;">{badge}</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;">
        {audio_card}
        {image_card}
      </div>
    </div>
    """


# -----------------------
# Pipeline entry
# -----------------------
def run_pipeline(
    view: str,
    age: int,
    sex: str,
    chief: str,
    history: str,
    intern_plan: str,
    audio_path,
    image,
    progress=gr.Progress(),
):
    patient = {
        "age": age,
        "sex": sex,
        "chief": chief,
        "history": history,
        "intern_plan": intern_plan,
    }

    result = orchestrator.run(
        view_mode=view,
        patient=patient,
        image=image,
        audio_path=audio_path,
        progress=progress,
    )

    transcript = result.get("audio_transcript", "") or ""

   
    meta = result.get("meta", {}) or {}
    meta_html = render_meta_panel(meta)

    # -----------------------
    # Patient View
    # -----------------------
    if result["mode"] == "patient":
        quiz = result["diagnosis"].get("quiz", [])
        qs = [q.get("question", "") for q in quiz] + [""] * 3
        opts = [q.get("options", []) for q in quiz] + [[]] * 3

        summary = build_patient_summary(result["diagnosis"])

        return (
            gr.update(value=meta_html, visible=True),  # meta_panel 
            gr.update(visible=False),                  # out_html
            gr.update(visible=True),                   # patient_area
            summary,                                   # out_patient_md
            gr.update(label=qs[0], choices=opts[0], value=None),
            gr.update(label=qs[1], choices=opts[1], value=None),
            gr.update(label=qs[2], choices=opts[2], value=None),
            quiz,                                      # quiz_state
            "",                                        # score_box
            transcript,                                # transcript_box
        )

    # -----------------------
    # Doctor View
    # -----------------------
    doctor_html = render_doctor_view_advanced(
        result["diagnosis"],
        result.get("audit", {}),
        result.get("reverse", {}),
        
    )

    return (
        gr.update(value=meta_html, visible=True),      # meta_panel 
        gr.update(value=doctor_html, visible=True),    # out_html 
        gr.update(visible=False),                      # patient_area
        "",                                            # out_patient_md
        gr.update(visible=False),                      # q1
        gr.update(visible=False),                      # q2
        gr.update(visible=False),                      # q3
        None,                                          # quiz_state
        "",                                            # score_box
        transcript,                                    # transcript_box
    )


# -----------------------
# Gradio UI
# -----------------------
with gr.Blocks(title="MedGemma V3 - Advanced") as demo:
    gr.Markdown("# MedGemma: Smart Clinical Assistant")
    gr.Markdown("Features: Vision | MedASR | Diagnosis | Self-Reflection | Differential Diagnosis")

    with gr.Row():
        with gr.Column(scale=1):
            age = gr.Number(label="Age", value=25)
            sex = gr.Dropdown(["Male", "Female"], value="Male", label="Sex")
            chief = gr.Textbox(label="Complaint", value="Chest pain, difficulty breathing.", lines=2)
            history = gr.Textbox(label="History", value="None")
            intern_plan = gr.Textbox(label="Intern Plan (Optional)")

        with gr.Column(scale=1):
            image = gr.Image(label="Image (Optional)", type="pil")
            audio = gr.Audio(label="Audio (Optional)", type="filepath")
            view = gr.Radio(["Doctor View", "Patient View"], value="Doctor View", label="Mode")
            btn = gr.Button("Run Full Analysis Pipeline", variant="primary")

           
            meta_panel = gr.HTML(label="Run Meta (route/basis/quality)")

           
            out_html = gr.HTML(label="Advanced Report")
            transcript_box = gr.Textbox(label="ASR Transcript", lines=6, interactive=False)

            with gr.Group(visible=False) as patient_area:
                out_patient_md = gr.Markdown()
                q1 = gr.Radio(label="Q1")
                q2 = gr.Radio(label="Q2")
                q3 = gr.Radio(label="Q3")
                ans_btn = gr.Button("Check Answers")
                score_box = gr.Markdown()

    quiz_state = gr.State([])

    btn.click(
        run_pipeline,
        inputs=[view, age, sex, chief, history, intern_plan, audio, image],
        outputs=[meta_panel, out_html, patient_area, out_patient_md, q1, q2, q3, quiz_state, score_box, transcript_box],
    )

    ans_btn.click(score_quiz, [q1, q2, q3, quiz_state], score_box)

PORT = int(os.getenv("PORT", "6006"))

demo.queue().launch(
    server_name="0.0.0.0",
    server_port=PORT,
    share=False,
)
