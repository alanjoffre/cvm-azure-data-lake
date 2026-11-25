"""Substituto local do ADF: as mesmas atividades, na mesma ordem, sobre pastas.

O ADF não tem emulador (D13). Para exercitar a lógica da Function na máquina, este módulo faz o
papel das atividades do ADF que movem dado (Copy, Delete) e dos dois pipelines
(``pl_inf_diario`` → ``pl_inf_diario_mes``). Ele NÃO é o que roda na Azure: lá, quem copia é a
atividade Copy, e o JSON dos pipelines está em ``adf/`` (docs/adf.md).

Diferenças conhecidas, declaradas em docs/testes.md:
- o Copy ZIP → Parquet é feito aqui pelo leitor de CSV do DuckDB com os tipos do dicionário; o
  tipo físico que o Copy do ADF grava só se confirma na Azure (D2b). Os testes cobrem as
  variantes "tudo como texto", "decimal como double" e a dos tipos do mapeamento em adf/
  (tests/test_hash_canonico.py e tests/test_adf.py);
- o JSON confere com Get Metadata se a prata existe antes de apagá-la; aqui o Delete já ignora
  a pasta que não existe, o que dá o mesmo resultado;
- o ForEach roda os meses em sequência (no ADF, lotes de 4 em paralelo; cada mês escreve só nos
  próprios caminhos, então a ordem não muda o resultado).
"""

from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import duckdb

from cvm_lake import esquema
from cvm_lake.funcao import prova
from cvm_lake.funcao.armazenamento import ArmazenamentoLocal
from cvm_lake.lake import Lake
from cvm_lake.origem import Origem

Resposta = dict[str, Any]
Gancho = Callable[[Lake, str, str], None]


class CargaFalhou(Exception):
    """A Function respondeu ``ok: false``: no ADF, a atividade Fail derruba o pipeline."""

    def __init__(self, resposta: Resposta) -> None:
        super().__init__(f"{resposta['etapa']} {resposta['competencia']}: {resposta['falhas']}")
        self.resposta = resposta


def novo_run_id() -> str:
    """O ADF identifica cada execução por um GUID (``pipeline().RunId``)."""
    return str(uuid.uuid4())


# --- Atividades -------------------------------------------------------------------------------


def copy_origem_para_bronze(origem: Origem, lake: Lake, competencia: str, carga: str) -> Resposta:
    """Passo 1 · Copy (binário) HTTP → bronze. A bronze é imutável: nunca sobrescreve (D5)."""
    arquivo = origem.baixar(competencia)
    destino = Path(lake.bronze_zip(competencia, carga))
    if destino.exists():
        raise FileExistsError(f"a bronze é imutável e {destino} já existe")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(arquivo.conteudo)
    return asdict(arquivo.metadados)


def copy_zip_para_staging(lake: Lake, competencia: str, carga: str) -> None:
    """Passo 2 · Copy DelimitedText (ZipDeflate, ``;``) → Parquet na staging.

    De propósito, o caminho de leitura é diferente do do validador: aqui o leitor de CSV já tipa
    (como o mapeamento do Copy), lá o CSV é lido como texto e convertido por CAST. Um defeito num
    leitor não passa escondido pelo outro.
    """
    conteudo = Path(lake.bronze_zip(competencia, carga)).read_bytes()
    destino = Path(lake.staging_parquet(competencia, carga))
    destino.parent.mkdir(parents=True, exist_ok=True)
    tipos = ", ".join(f"'{c.nome}': '{c.tipo}'" for c in esquema.COLUNAS)
    with tempfile.TemporaryDirectory() as tmp:
        csv = Path(tmp) / "entrada.csv"
        csv.write_bytes(prova.csv_do_zip(conteudo))
        con = duckdb.connect(":memory:")
        con.execute(f"""
            COPY (SELECT * FROM read_csv('{csv}', header = true, delim = ';',
                                         columns = {{{tipos}}}))
            TO '{destino}' (FORMAT parquet)
        """)


def delete(pasta: str) -> None:
    """Atividade Delete: apaga a pasta inteira, sem erro se ela não existir."""
    shutil.rmtree(pasta, ignore_errors=True)


def copy_pasta(origem: str, destino: str) -> None:
    """Passo 5 · Copy (binário) staging → prata: arquivo por arquivo, sem reprocessar."""
    Path(destino).mkdir(parents=True, exist_ok=True)
    for arquivo in sorted(Path(origem).glob("*.parquet")):
        shutil.copy2(arquivo, Path(destino) / arquivo.name)


# --- Pipelines --------------------------------------------------------------------------------


def pl_inf_diario_mes(
    origem: Origem,
    lake: Lake,
    competencia: str,
    carga: str,
    depois_da_staging: Gancho | None = None,
) -> Resposta:
    """O pipeline filho: um mês, os 7 passos de docs/arquitetura.md, seção 3.

    ``depois_da_staging`` é o ponto de injeção de falha da prova (corromper a staging entre os
    passos 2 e 3). No ADF não existe.
    """
    arm = ArmazenamentoLocal()
    inicio = time.perf_counter()
    meta = copy_origem_para_bronze(origem, lake, competencia, carga)  # passo 1
    copy_zip_para_staging(lake, competencia, carga)  # passo 2
    if depois_da_staging is not None:
        depois_da_staging(lake, competencia, carga)
    validacao = prova.validar(arm, lake, competencia, carga)  # passo 3 (Function)
    if not validacao["ok"]:
        raise CargaFalhou(validacao)  # a prata não foi tocada; a staging fica para diagnóstico
    delete(lake.prata(competencia))  # passo 4
    copy_pasta(lake.staging(competencia, carga), lake.prata(competencia))  # passo 5
    reconciliacao = prova.reconciliar(arm, lake, competencia, carga, meta)  # passo 6 (Function)
    if not reconciliacao["ok"]:
        raise CargaFalhou(reconciliacao)
    delete(lake.staging(competencia, carga))  # passo 7
    return {
        "competencia": competencia,
        "carga": carga,
        "origem": meta,
        "validacao": validacao,
        "reconciliacao": reconciliacao,
        "segundos": round(time.perf_counter() - inicio, 3),
    }


def pl_inf_diario(
    origem: Origem,
    lake: Lake,
    competencias: list[str],
    carga: str | None = None,
    depois_da_staging: Gancho | None = None,
) -> list[Resposta]:
    """O pipeline pai: ForEach competência → Execute Pipeline do filho.

    Como no ADF, a falha de um mês não impede os outros; o pai falha no fim, com a lista. Vale
    para a Function respondendo ``ok: false`` e para qualquer atividade que quebre (um Copy que
    não lê o ZIP, por exemplo).
    """
    carga = carga or novo_run_id()
    resultados: list[Resposta] = []
    falhas: list[Exception] = []
    for competencia in dict.fromkeys(competencias):  # union() no ADF: mês repetido roda uma vez
        try:
            resultados.append(
                pl_inf_diario_mes(origem, lake, competencia, carga, depois_da_staging)
            )
        except Exception as erro:  # noqa: BLE001 - o ForEach do ADF também segue
            falhas.append(erro)
    if falhas:
        raise ExceptionGroup("pl_inf_diario falhou", falhas)
    return resultados
