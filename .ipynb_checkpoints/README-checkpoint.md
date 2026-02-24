# MedGemma Impact Challenge

## Overview
This project provides a Gradio app that runs MedGemma and MedSigLIP with a RAG
pipeline for clinical analysis.

## Run
```powershell
python app.py
```

## Model Downloads
- Hugging Face models (MedGemma and MedSigLIP) are cached under the repository
  `models` directory via `cache_dir` in `src/agents/observer.py`.
- The RAG embedding model is stored under `models/rag/embeddings` by default,
  configured in `src/tools/rag_engine.py`.

## Notes
- If you want a different model directory, update the `cache_dir` usage in
  `src/agents/observer.py` and the `DEFAULT_EMBEDDING_MODEL` path in
  `src/tools/rag_engine.py`.
