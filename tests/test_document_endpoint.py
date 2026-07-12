"""Regression tests for GET /document/{piece_id} (src/aria_rag/api.py).

Bypasses the app's lifespan (which loads the embedding model, FAISS index,
and Ollama backend check) by constructing app.state directly — this
endpoint only ever touches app.state.settings and app.state.referentiel.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from aria_rag.api import app
from aria_rag.config import Settings
from aria_rag.referentiel import PieceFile, Referentiel


def _client(tmp_path: Path, files: list[PieceFile]) -> TestClient:
    docs_dir = tmp_path / "Ressources"
    docs_dir.mkdir(parents=True, exist_ok=True)
    app.state.settings = Settings(docs_dir=docs_dir, index_dir=tmp_path / "index")
    app.state.referentiel = Referentiel(
        pieces=[],  # populated per-test via _add_piece below
        files=files,
    )
    return TestClient(app)


def _add_piece(client: TestClient, piece_id: str) -> None:
    from aria_rag.referentiel import Piece
    app.state.referentiel.pieces.append(Piece(
        id=piece_id, official_name=piece_id, status_opposabilite="opposable",
        family="annexes", norm_level="local", expected=True, file_match=None, notes="",
    ))


def test_document_endpoint_serves_single_current_file(tmp_path):
    docs_dir = tmp_path / "Ressources"
    (docs_dir / "Tome1").mkdir(parents=True)
    (docs_dir / "Tome1" / "REG1_MS1.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    files = [PieceFile(path="Tome1/REG1_MS1.pdf", piece_id="reg-ecrit-t1", validity="current", chunk_count=5, pages=10)]
    client = _client(tmp_path, files)
    _add_piece(client, "reg-ecrit-t1")

    resp = client.get("/document/reg-ecrit-t1")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "inline" in resp.headers["content-disposition"]
    assert resp.content == b"%PDF-1.4\n%%EOF"


def test_document_endpoint_404_for_unknown_piece(tmp_path):
    client = _client(tmp_path, [])
    resp = client.get("/document/does-not-exist")
    assert resp.status_code == 404


def test_document_endpoint_404_for_multi_file_piece(tmp_path):
    docs_dir = tmp_path / "Ressources"
    (docs_dir / "Atlas1").mkdir(parents=True)
    (docs_dir / "Atlas1" / "a.pdf").write_bytes(b"%PDF-1.4")
    (docs_dir / "Atlas1" / "b.pdf").write_bytes(b"%PDF-1.4")
    files = [
        PieceFile(path="Atlas1/a.pdf", piece_id="atlas1", validity="current", chunk_count=1, pages=1),
        PieceFile(path="Atlas1/b.pdf", piece_id="atlas1", validity="current", chunk_count=1, pages=1),
    ]
    client = _client(tmp_path, files)
    _add_piece(client, "atlas1")

    resp = client.get("/document/atlas1")
    assert resp.status_code == 404


def test_document_endpoint_404_for_expected_absent_piece(tmp_path):
    client = _client(tmp_path, [])
    _add_piece(client, "annexe-sanitaire")
    resp = client.get("/document/annexe-sanitaire")
    assert resp.status_code == 404
