"""O hash canônico (D7): o que ele precisa garantir e o que ele precisa denunciar."""

from __future__ import annotations

import random
from pathlib import Path

import duckdb
import pytest
from conftest import linhas_csv, montar_csv, montar_zip

from cvm_lake import esquema
from cvm_lake.funcao.prova import ErroDeLayout, resumir_csv, resumir_parquet
from cvm_lake.referencia import sha256_canonico_csv

POS = {nome: i for i, nome in enumerate(esquema.NOMES)}


def _resumo_csv(csv: bytes) -> tuple[int, str]:
    r = resumir_csv(duckdb.connect(":memory:"), csv)
    return r.linhas, r.sha256


def _parquet(tmp_path: Path, csv: bytes, tipos: dict[str, str], nome: str) -> str:
    """Grava o CSV como Parquet com os tipos físicos pedidos (simula o que o Copy gravaria)."""
    entrada = tmp_path / f"{nome}.csv"
    entrada.write_bytes(csv)
    saida = tmp_path / f"{nome}.parquet"
    colunas = ", ".join(f"'{n}': '{tipos.get(n, 'VARCHAR')}'" for n in esquema.NOMES)
    duckdb.connect(":memory:").execute(f"""
        COPY (SELECT * FROM read_csv('{entrada}', header = true, delim = ';',
                                     columns = {{{colunas}}}))
        TO '{saida}' (FORMAT parquet)""")
    return str(saida)


def _resumo_parquet(caminho: str) -> tuple[int, str]:
    r = resumir_parquet(duckdb.connect(":memory:"), caminho, "t")
    return r.linhas, r.sha256


@pytest.fixture
def csv() -> bytes:
    return montar_csv(linhas_csv("202610"))


def test_referencia_python_igual_ao_duckdb(csv: bytes) -> None:
    # O sintético tem cota zero: str(Decimal) daria "0E-12" e o hash divergiria.
    assert sha256_canonico_csv(csv) == _resumo_csv(csv)


def test_csv_parquet_tipado_e_parquet_texto_tem_o_mesmo_hash(csv: bytes, tmp_path: Path) -> None:
    tipado = {c.nome: c.tipo for c in esquema.COLUNAS}
    assert _resumo_parquet(_parquet(tmp_path, csv, tipado, "tipado")) == _resumo_csv(csv)
    assert _resumo_parquet(_parquet(tmp_path, csv, {}, "texto")) == _resumo_csv(csv)


DOUBLE = {c.nome: c.tipo for c in esquema.COLUNAS} | {"VL_QUOTA": "DOUBLE"}


def test_cota_longa_gravada_como_double_muda_o_hash(tmp_path: Path) -> None:
    """D2b: se o Copy gravar a cota como double, a reconciliação precisa acusar a perda.

    A cota real chega a 21 dígitos significativos (112439113.205550566316, out/2026); o double
    guarda uns 15. Medido nos 3 meses reais: 1.923, 1.866 e 1.077 cotas mudariam.
    """
    linhas = [list(linha) for linha in linhas_csv("202610")]
    linhas[0][POS["VL_QUOTA"]] = "112439113.205550566316"
    csv = montar_csv(linhas)
    linhas_pq, sha = _resumo_parquet(_parquet(tmp_path, csv, DOUBLE, "double"))
    assert linhas_pq == _resumo_csv(csv)[0]
    assert sha != _resumo_csv(csv)[1]


def test_cota_curta_sobrevive_ao_double(csv: bytes, tmp_path: Path) -> None:
    """O hash compara valores, não tipos físicos: se o double não perdeu nada, não acusa."""
    assert _resumo_parquet(_parquet(tmp_path, csv, DOUBLE, "double")) == _resumo_csv(csv)


def test_ordem_fisica_nao_muda_o_hash() -> None:
    linhas = linhas_csv("202610")
    embaralhadas = list(linhas)
    random.Random(1).shuffle(embaralhadas)
    assert _resumo_csv(montar_csv(embaralhadas)) == _resumo_csv(montar_csv(linhas))


def test_um_centavo_muda_o_hash() -> None:
    linhas = linhas_csv("202610")
    alterada = [list(linha) for linha in linhas]
    alterada[10][POS["RESG_DIA"]] = f"{float(alterada[10][POS['RESG_DIA']]) + 0.01:.2f}"
    assert _resumo_csv(montar_csv(alterada))[1] != _resumo_csv(montar_csv(linhas))[1]


def test_subclasse_vazia_e_nulo_e_ordena_primeiro(tmp_path: Path) -> None:
    linhas = [linha for linha in linhas_csv("202610") if linha[POS["CNPJ_FUNDO_CLASSE"]]
              == linhas_csv("202610")[0][POS["CNPJ_FUNDO_CLASSE"]]]
    assert {linha[POS["ID_SUBCLASSE"]] for linha in linhas} == {"", "SUB000"}
    con = duckdb.connect(":memory:")
    resumir_csv(con, montar_csv(linhas))
    primeira = con.execute(f"SELECT {esquema.sql_linha_canonica()} FROM bronze AS t "
                           f"ORDER BY {esquema.sql_ordem_chave()} LIMIT 1").fetchone()
    assert primeira is not None and primeira[0].split("|")[2] == esquema.NULO_CANONICO


def test_casa_decimal_a_mais_fica_fora_do_dicionario() -> None:
    linhas = [list(linha) for linha in linhas_csv("202610")]
    linhas[0][POS["VL_TOTAL"]] = "10.123"
    resumo = resumir_csv(duckdb.connect(":memory:"), montar_csv(linhas))
    assert resumo.fora_do_dicionario == 1
    assert resumo.sha256 == ""  # não tipa: o CAST arredondaria e esconderia o motivo


def test_chave_duplicada_e_contada() -> None:
    linhas = linhas_csv("202610")
    resumo = resumir_csv(duckdb.connect(":memory:"), montar_csv([*linhas, linhas[3]]))
    assert resumo.chaves_duplicadas == 1


def test_cabecalho_diferente_e_erro_de_layout() -> None:
    cabecalho = [*esquema.NOMES[:-1], "NR_COTISTAS"]
    with pytest.raises(ErroDeLayout):
        resumir_csv(duckdb.connect(":memory:"), montar_csv(linhas_csv("202610"), cabecalho))


def test_zip_com_dois_csv_e_erro_de_layout() -> None:
    import io
    import zipfile

    from cvm_lake.funcao.prova import csv_do_zip

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as arquivo:
        arquivo.writestr("a.csv", b"x")
        arquivo.writestr("b.csv", b"y")
    with pytest.raises(ErroDeLayout):
        csv_do_zip(saida.getvalue())
    assert csv_do_zip(montar_zip(b"abc", "202610")) == b"abc"
