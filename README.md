# ARIA

Starter repository for a document RAG workflow over the PDFs already stored in `Ressources/`.

## What is included

- A fresh Git repository in this folder.
- A Python CLI to ingest PDFs into a local search index.
- A retrieval flow that works locally with TF-IDF.
- Optional answer generation with OpenAI or a local Ollama model.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
cp .env.example .env
```

Build the first index:

```bash
aria-rag ingest --max-files 20
```

For the full corpus, ingestion can take a while because PDF text extraction is expensive. The CLI now:

- uses a conservative default worker count for laptops
- prints per-file progress with `PROCESSED` or `CACHED`
- reuses unchanged PDFs on later runs

You can still override the worker count manually:

```bash
aria-rag ingest --workers 4
aria-rag ingest --rebuild
```

Ask a question:

```bash
aria-rag ask "What are the construction rules for this area?" --no-llm
```

If you add `OPENAI_API_KEY` in `.env`, you can omit `--no-llm` and get a synthesized answer grounded in retrieved passages.

For a local LLM on your MacBook Air M2, install Ollama, pull a small model, and use the Ollama backend:

```bash
ollama pull gemma3:4b
aria-rag ask "What are the construction rules for this area?" --backend ollama
```

Suggested starting point on an M2 Air:

- `gemma3:4b`
- keep queries short
- use retrieval first, generation second

## Project structure

```text
Ressources/          Source PDFs
data/index/          Generated local index
src/aria_rag/        RAG starter package
```

## Link to GitHub later

When you create the remote repository, run:

```bash
git remote add origin <your-repository-url>
git branch -M main
git add .
git commit -m "Initial ARIA RAG scaffold"
git push -u origin main
```

## Suggested next steps

1. Run a small ingestion batch with `--max-files` to validate extraction quality.
2. Inspect the retrieved passages for a few real questions.
3. Replace the local TF-IDF retriever with embeddings + vector DB once the document pipeline is stable.
4. Add metadata filters for document families such as `PLU bioclimatique`, annexes, and regulations.
