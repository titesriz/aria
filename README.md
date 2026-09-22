# ARIA

RAG pipeline for architects, answering regulatory questions grounded in the PLU bioclimatique de Paris and the CCH. Currently a proof of concept — no bundled UI yet (planned for MVP); this repo is the backend/API/CLI only.

**Full architecture, module map, data flow, and current open issues: see [`PROJECT_MAP.md`](PROJECT_MAP.md).** This file is just quick-start commands.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env   # fill in model choices; OpenAI/Anthropic keys only needed if you use those backends
```

Ollama is the default local backend (no API key needed) — see `.env.example` for the expansion/synthesis model split.

## Build the index

```bash
aria-rag ingest --max-files 20      # quick smoke test
aria-rag ingest                     # full corpus; reuses extraction+embedding for unchanged files, but rebuilds the FAISS/BM25 index in full
aria-rag ingest --rebuild --family reglement_ecrit   # scoped rebuild of one family
aria-rag check                      # validate the index against corpus invariants
```

Retrieval is a hybrid FAISS (dense) + BM25 (lexical) index, fused by Reciprocal Rank Fusion, scoped per document family. Not TF-IDF.

## Ask a question

```bash
aria-rag ask "Quelle est la hauteur maximale en zone UG ?" --no-llm   # retrieval only
aria-rag ask "Quelle est la hauteur maximale en zone UG ?"            # + LLM-synthesized answer
```

## Run the API

```bash
aria-rag serve   # FastAPI on :8000 — POST /ask, POST /feedback, GET /document/{piece_id}, GET /health
```

## Evaluate

```bash
aria-rag eval --no-llm   # golden-dataset retrieval scoring
```

See `eval/README.md` for the methodology and `CLAUDE.md` §4 for why golden-dataset scores aren't a certified ceiling.
