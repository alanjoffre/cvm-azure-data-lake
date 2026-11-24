"""Validação (antes da troca da partição) e reconciliação (depois), passos 3 e 6 do filho.

As duas respondem às mesmas perguntas (docs/arquitetura.md, seção 5):

1. Não perdeu e não duplicou? Contagem do CSV na bronze = contagem do Parquet, e chave única.
2. Não alterou nada no caminho? SHA-256 canônico da bronze = SHA-256 canônico do Parquet.

A validação compara bronze × staging e decide se o ADF pode apagar e regravar a prata. A
reconciliação compara bronze × prata depois da troca, refaz o ouro do mês, confere o ouro contra
a prata e só então grava o manifesto (a carga vigente do mês).

Toda falha vira ``ok: false`` com a lista de motivos, nunca exceção para fora: a Function devolve
o JSON com status de erro, e o ADF falha a atividade.
"""

from __future__ import annotations

import io
import json
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from cvm_lake import esquema
from cvm_lake.funcao import ouro as sql_ouro
from cvm_lake.funcao.armazenamento import Armazenamento
from cvm_lake.lake import Lake, conferir_carga, conferir_competencia, parquets

Resposta = dict[str, Any]


class ErroDeLayout(Exception):
    """O arquivo da CVM não tem o formato esperado (cabeçalho, membros do ZIP)."""


@dataclass(frozen=True)
class Resumo:
    linhas: int
    sha256: str
    chaves_duplicadas: int


@dataclass(frozen=True)
class ResumoBronze(Resumo):
    linhas_brutas: int  # linhas físicas do CSV, sem o cabeçalho, contadas nos bytes
    fora_do_dicionario: int  # valores que não casam com o padrão do dicionário


def _literal(texto: str) -> str:
    return "'" + texto.replace("'", "''") + "'"


def _conectar() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


def _um(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    linha = con.execute(sql).fetchone()
    if linha is None:  # agregados sempre devolvem uma linha; isto é só para o mypy
        raise RuntimeError("consulta sem resultado")
    return linha


def csv_do_zip(conteudo_zip: bytes) -> bytes:
    """O ZIP da CVM tem exatamente um CSV. Mais de um, ou nenhum, é mudança de layout."""
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as arquivo:
        membros = [m for m in arquivo.infolist() if not m.is_dir()]
        if len(membros) != 1 or not membros[0].filename.lower().endswith(".csv"):
            nomes = [m.filename for m in membros]
            raise ErroDeLayout(f"esperado 1 CSV no ZIP, encontrado {nomes}")
        return arquivo.read(membros[0])


def resumir_csv(con: duckdb.DuckDBPyConnection, csv: bytes) -> ResumoBronze:
    """Lê o CSV da CVM como texto, confere contra o dicionário, tipa e resume.

    O CSV é lido com todas as colunas como VARCHAR e sem detecção automática: o tipo vem do
    dicionário (esquema.py), nunca do que o leitor adivinhar.
    """
    cabecalho_esperado = esquema.CSV_DELIMITADOR.join(esquema.NOMES).encode(esquema.CSV_ENCODING)
    primeira_linha = csv.split(b"\n", 1)[0].rstrip(b"\r")
    if primeira_linha != cabecalho_esperado:
        raise ErroDeLayout(f"cabeçalho diferente do dicionário: {primeira_linha[:200]!r}")

    linhas_brutas = csv.count(b"\n") - 1 + (0 if csv.endswith(b"\n") else 1)

    colunas = ", ".join(f"{_literal(n)}: 'VARCHAR'" for n in esquema.NOMES)
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / "bronze.csv"
        caminho.write_bytes(csv)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE bronze_texto AS
            SELECT * FROM read_csv({_literal(str(caminho))},
                auto_detect = false, header = true, delim = {_literal(esquema.CSV_DELIMITADOR)},
                columns = {{{colunas}}}, strict_mode = true, null_padding = false)
        """)

    fora = int(_um(con, esquema.sql_fora_do_dicionario("bronze_texto"))[0])
    if fora:
        # Não tipar: o CAST arredondaria ou falharia, e o motivo real ficaria escondido.
        linhas = int(_um(con, "SELECT count(*) FROM bronze_texto")[0])
        return ResumoBronze(linhas, "", 0, linhas_brutas, fora)

    con.execute(f"CREATE OR REPLACE TEMP TABLE bronze AS {esquema.sql_tipado('bronze_texto')}")
    linhas, sha, dup = _um(con, esquema.sql_resumo("bronze"))
    return ResumoBronze(int(linhas), str(sha), int(dup), linhas_brutas, 0)


def resumir_parquet(con: duckdb.DuckDBPyConnection, glob: str, tabela: str) -> Resumo:
    """Tipa o Parquet pelo dicionário (qualquer que seja o tipo físico gravado) e resume."""
    origem = f"read_parquet({_literal(glob)})"
    con.execute(f"CREATE OR REPLACE TEMP TABLE {tabela} AS {esquema.sql_tipado(origem)}")
    linhas, sha, dup = _um(con, esquema.sql_resumo(tabela))
    return Resumo(int(linhas), str(sha), int(dup))


def _comparar(bronze: ResumoBronze | None, destino: Resumo | None, nome: str) -> list[str]:
    falhas: list[str] = []
    if bronze is None or destino is None:
        return falhas  # o erro de leitura já foi registrado
    if bronze.fora_do_dicionario:
        falhas.append(f"bronze: {bronze.fora_do_dicionario} linhas com valor fora do dicionário")
    if bronze.linhas != bronze.linhas_brutas:
        falhas.append(f"bronze: o leitor viu {bronze.linhas} linhas, o arquivo tem "
                      f"{bronze.linhas_brutas}")
    if bronze.chaves_duplicadas:
        falhas.append(f"bronze: {bronze.chaves_duplicadas} chaves duplicadas")
    if destino.chaves_duplicadas:
        falhas.append(f"{nome}: {destino.chaves_duplicadas} chaves duplicadas")
    if bronze.linhas != destino.linhas:
        falhas.append(f"contagem: bronze {bronze.linhas} × {nome} {destino.linhas}")
    if bronze.sha256 and bronze.sha256 != destino.sha256:
        falhas.append(f"sha256: bronze {bronze.sha256[:12]}… × {nome} {destino.sha256[:12]}…")
    return falhas


def _agora() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _ler_json(arm: Armazenamento, caminho: str) -> dict[str, Any]:
    if not arm.existe(caminho):
        return {}
    dado: dict[str, Any] = json.loads(arm.ler_bytes(caminho))
    return dado


def _gravar_json(arm: Armazenamento, caminho: str, dado: dict[str, Any]) -> None:
    texto = json.dumps(dado, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    arm.gravar_bytes(caminho, texto.encode("utf-8"))


def validar(arm: Armazenamento, lake: Lake, competencia: str, carga: str) -> Resposta:
    """Passo 3: bronze × staging. ``ok`` libera o ADF para apagar e regravar a prata."""
    inicio = time.perf_counter()
    competencia, carga = conferir_competencia(competencia), conferir_carga(carga)
    falhas: list[str] = []
    bronze: ResumoBronze | None = None
    staging: Resumo | None = None
    try:
        con = _conectar()
        bronze = resumir_csv(con, csv_do_zip(arm.ler_bytes(lake.bronze_zip(competencia, carga))))
        staging = resumir_parquet(
            con, arm.caminho_duckdb(parquets(lake.staging(competencia, carga))), "staging")
    except (duckdb.Error, ErroDeLayout, OSError, zipfile.BadZipFile) as erro:
        falhas.append(f"{type(erro).__name__}: {erro}")
    falhas += _comparar(bronze, staging, "staging")

    resposta: Resposta = {
        "etapa": "validar",
        "ok": not falhas,
        "competencia": competencia,
        "carga": carga,
        "bronze": asdict(bronze) if bronze else None,
        "staging": asdict(staging) if staging else None,
        "falhas": falhas,
        "segundos": round(time.perf_counter() - inicio, 3),
        "em": _agora(),
    }
    _gravar_json(arm, lake.relatorio_carga(competencia, carga), {"validacao": resposta})
    return resposta


def reconciliar(arm: Armazenamento, lake: Lake, competencia: str, carga: str,
                origem: dict[str, Any] | None = None) -> Resposta:
    """Passo 6: bronze × prata, ouro refeito e conferido, manifesto gravado se tudo bater.

    ``origem`` traz os metadados HTTP da CVM no momento da cópia (URL, ETag, Last-Modified),
    que o manifesto guarda para a otimização futura do D3.
    """
    inicio = time.perf_counter()
    competencia, carga = conferir_competencia(competencia), conferir_carga(carga)
    falhas: list[str] = []
    bronze: ResumoBronze | None = None
    prata: Resumo | None = None
    conferencia_ouro: dict[str, Any] | None = None
    try:
        con = _conectar()
        bronze = resumir_csv(con, csv_do_zip(arm.ler_bytes(lake.bronze_zip(competencia, carga))))
        prata = resumir_parquet(
            con, arm.caminho_duckdb(parquets(lake.prata(competencia))), "prata")
        falhas += _comparar(bronze, prata, "prata")
        if not falhas:
            con.execute(f"CREATE OR REPLACE TEMP TABLE ouro AS {sql_ouro.SQL_OURO}")
            linha = _um(con, sql_ouro.SQL_CONFERE_OURO)
            nomes = [d[0] for d in con.description or []]
            conferencia_ouro = {n: (str(v) if not isinstance(v, int) else v)
                                for n, v in zip(nomes, linha, strict=True)}
            for a, b in (("linhas_prata", "linhas_no_ouro"), ("captacao_prata", "captacao_ouro"),
                         ("resgate_prata", "resgate_ouro")):
                if conferencia_ouro[a] != conferencia_ouro[b]:
                    falhas.append(f"ouro: {a} {conferencia_ouro[a]} × {b} {conferencia_ouro[b]}")
        if not falhas:
            with tempfile.TemporaryDirectory() as pasta:
                arquivo = Path(pasta) / "ouro.parquet"
                con.execute(f"COPY ouro TO {_literal(str(arquivo))} (FORMAT parquet)")
                arm.gravar_bytes(f"{lake.ouro(competencia)}/ouro.parquet", arquivo.read_bytes())
    except (duckdb.Error, ErroDeLayout, OSError, zipfile.BadZipFile) as erro:
        falhas.append(f"{type(erro).__name__}: {erro}")

    resposta: Resposta = {
        "etapa": "reconciliar",
        "ok": not falhas,
        "competencia": competencia,
        "carga": carga,
        "bronze": asdict(bronze) if bronze else None,
        "prata": asdict(prata) if prata else None,
        "ouro": conferencia_ouro,
        "falhas": falhas,
        "segundos": round(time.perf_counter() - inicio, 3),
        "em": _agora(),
    }
    caminho_relatorio = lake.relatorio_carga(competencia, carga)
    relatorio = _ler_json(arm, caminho_relatorio)
    relatorio["reconciliacao"] = resposta
    _gravar_json(arm, caminho_relatorio, relatorio)

    if resposta["ok"] and prata is not None:
        _gravar_json(arm, lake.manifesto(competencia), {
            "competencia": competencia,
            "carga_vigente": carga,
            "linhas": prata.linhas,
            "sha256": prata.sha256,
            "origem": origem or {},
            "reconciliado_em": resposta["em"],
        })
    return resposta
