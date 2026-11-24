"""Janela, layout do lake, republicação simulada e origens (HTTP e pasta)."""

from __future__ import annotations

import hashlib
import http.server
import json
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from conftest import zip_sintetico

from cvm_lake.funcao.prova import csv_do_zip
from cvm_lake.janela import JANELA_DIARIA, JANELA_SEMANAL, competencia_menos, janela
from cvm_lake.lake import Lake
from cvm_lake.origem import OrigemHTTP, OrigemPasta, guardar, nome_arquivo
from cvm_lake.referencia import sha256_canonico_csv
from cvm_lake.republicacao import gerar_t1

# --- janela (D3) ------------------------------------------------------------------------------


def test_janela_diaria_e_semanal_em_24_11_2026() -> None:
    hoje = date(2026, 11, 24)
    assert janela(hoje, *JANELA_DIARIA) == ["202611", "202610"]
    semanal = janela(hoje, *JANELA_SEMANAL)
    assert semanal[0] == "202609" and semanal[-1] == "202512" and len(semanal) == 10


def test_janela_na_virada_do_ano() -> None:
    assert janela(date(2027, 1, 5), *JANELA_DIARIA) == ["202701", "202612"]
    assert competencia_menos(date(2027, 2, 28), 11) == "202603"
    with pytest.raises(ValueError):
        competencia_menos(date(2027, 1, 1), -1)
    with pytest.raises(ValueError):
        janela(date(2027, 1, 1), 3, 2)


# --- lake -------------------------------------------------------------------------------------


@pytest.mark.parametrize("competencia", ["202613", "20261", "2026-10", "../x"])
def test_competencia_invalida(competencia: str) -> None:
    with pytest.raises(ValueError):
        Lake("raiz").prata(competencia)


@pytest.mark.parametrize("carga", ["", "a/b", "..", "x" * 65])
def test_carga_invalida(carga: str) -> None:
    with pytest.raises(ValueError):
        Lake("raiz").staging("202610", carga)


def test_mesmo_layout_para_pasta_e_adls() -> None:
    adls = Lake("abfss://lake@conta.dfs.core.windows.net/")
    assert adls.prata("202610") == \
        "abfss://lake@conta.dfs.core.windows.net/prata/cvm/inf_diario/competencia=202610"


# --- republicação simulada ----------------------------------------------------------------------


def test_republicacao_e_deterministica_e_so_o_hash_denuncia() -> None:
    t0 = zip_sintetico("202610")
    t1a, mudancas_a = gerar_t1(t0)
    t1b, mudancas_b = gerar_t1(t0)
    assert csv_do_zip(t1a) == csv_do_zip(t1b) and mudancas_a == mudancas_b
    linhas_t0, sha_t0 = sha256_canonico_csv(csv_do_zip(t0))
    linhas_t1, sha_t1 = sha256_canonico_csv(csv_do_zip(t1a))
    assert linhas_t1 == linhas_t0 and sha_t1 != sha_t0
    assert len(mudancas_a.alteradas) == 3
    assert mudancas_a.removida not in mudancas_a.alteradas
    assert mudancas_a.incluida is not None and mudancas_a.incluida[2].startswith("2026-10")


def test_republicacao_mantem_o_formato_fisico() -> None:
    csv = csv_do_zip(gerar_t1(zip_sintetico("202610"))[0])
    assert csv.endswith(b"\r\n") and b'"' not in csv
    csv.decode("ascii")


# --- origens ----------------------------------------------------------------------------------


@pytest.fixture
def servidor(tmp_path: Path) -> Iterator[str]:
    pasta = tmp_path / "www"
    pasta.mkdir()
    (pasta / nome_arquivo("202610")).write_bytes(zip_sintetico("202610"))

    class Manipulador(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(pasta), **kwargs)  # type: ignore[arg-type]

        def end_headers(self) -> None:
            self.send_header("ETag", '"abc-123"')
            super().end_headers()

        def log_message(self, *args: object) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Manipulador)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_origem_http_traz_os_metadados(servidor: str, tmp_path: Path) -> None:
    arquivo = OrigemHTTP(servidor, timeout=10).baixar("202610")
    assert arquivo.conteudo == zip_sintetico("202610")
    meta = arquivo.metadados
    assert meta.etag == '"abc-123"' and meta.last_modified is not None
    assert meta.sha256_zip == hashlib.sha256(arquivo.conteudo).hexdigest()
    assert meta.url.endswith("/inf_diario_fi_202610.zip")

    # Guardado e lido de volta pela OrigemPasta: mesmos bytes, mesmos metadados.
    guardar(arquivo, tmp_path / "snap", "202610")
    relido = OrigemPasta(tmp_path / "snap").baixar("202610")
    assert relido == arquivo


def test_origem_pasta_recusa_zip_trocado(servidor: str, tmp_path: Path) -> None:
    guardar(OrigemHTTP(servidor, timeout=10).baixar("202610"), tmp_path / "snap", "202610")
    (tmp_path / "snap" / nome_arquivo("202610")).write_bytes(zip_sintetico("202609"))
    with pytest.raises(OSError, match="não é o descrito"):
        OrigemPasta(tmp_path / "snap").baixar("202610")


def test_origem_pasta_sem_metadados(tmp_path: Path) -> None:
    (tmp_path / nome_arquivo("202610")).write_bytes(b"zip")
    meta = OrigemPasta(tmp_path).baixar("202610").metadados
    assert meta.url.startswith("file://") and meta.etag is None
    assert json.dumps(meta.__dict__)
