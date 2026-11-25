"""Validação (antes da troca da partição) e reconciliação (depois), passos 3 e 6 do filho.

As duas respondem às mesmas perguntas (docs/arquitetura.md, seção 5):

1. Não perdeu e não duplicou? Contagem do CSV na bronze = contagem do Parquet, e chave única.
2. Não alterou nada no caminho? SHA-256 canônico da bronze = SHA-256 canônico do Parquet.

A validação compara bronze × staging e decide se o ADF pode apagar e regravar a prata. A
reconciliação compara bronze × prata depois da troca, refaz o ouro do mês, confere o ouro contra
a prata e só então grava o manifesto (a carga vigente do mês).

Toda falha vira ``ok: false`` com a lista de motivos, nunca exceção para fora: a Function devolve
o JSON (``entrada.py``), e a guarda Fail do pipeline derruba a carga com as falhas na mensagem.
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
    fora_do_dicionario: int  # linhas com algum valor fora do padrão do dicionário


def literal(texto: str) -> str:
    """Literal de texto do SQL do DuckDB, com a aspa simples escapada."""
    return "'" + texto.replace("'", "''") + "'"


def uma_linha(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Any, ...]:
    """A única linha de uma consulta agregada."""
    linha = con.execute(sql).fetchone()
    if linha is None:  # agregados sempre devolvem uma linha; isto é só para o mypy
        raise RuntimeError("consulta sem resultado")
    return linha


def _n(quantidade: int, singular: str, plural: str) -> str:
    """ "1 linha", "2 linhas": a mensagem de falha concorda com o número."""
    return f"{quantidade} {singular if quantidade == 1 else plural}"


def csv_do_zip(conteudo_zip: bytes) -> bytes:
    """O ZIP da CVM tem exatamente um CSV. Mais de um, ou nenhum, é mudança de layout."""
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as arquivo:
        membros = [m for m in arquivo.infolist() if not m.is_dir()]
        if len(membros) != 1 or not membros[0].filename.lower().endswith(".csv"):
            nomes = [m.filename for m in membros]
            raise ErroDeLayout(f"esperado 1 CSV no ZIP, encontrados: {nomes}")
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

    # Linhas físicas do CSV, contadas nos bytes e à parte do leitor (menos o cabeçalho; a última
    # pode vir sem quebra de linha): se o leitor juntar ou pular linha, _comparar acusa a diferença.
    linhas_brutas = csv.count(b"\n") - 1 + (0 if csv.endswith(b"\n") else 1)

    colunas = ", ".join(f"{literal(n)}: 'VARCHAR'" for n in esquema.NOMES)
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / "bronze.csv"
        caminho.write_bytes(csv)
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE bronze_texto AS
            SELECT * FROM read_csv({literal(str(caminho))},
                auto_detect = false, header = true, delim = {literal(esquema.CSV_DELIMITADOR)},
                columns = {{{colunas}}}, strict_mode = true, null_padding = false)
        """)

    fora = int(uma_linha(con, esquema.sql_fora_do_dicionario("bronze_texto"))[0])
    if fora:
        # Não tipar: o CAST arredondaria ou falharia, e o motivo real ficaria escondido.
        linhas = int(uma_linha(con, "SELECT count(*) FROM bronze_texto")[0])
        return ResumoBronze(
            linhas=linhas,
            sha256="",
            chaves_duplicadas=0,
            linhas_brutas=linhas_brutas,
            fora_do_dicionario=fora,
        )

    con.execute(f"CREATE OR REPLACE TEMP TABLE bronze AS {esquema.sql_tipado('bronze_texto')}")
    linhas, sha, dup = uma_linha(con, esquema.sql_resumo("bronze"))
    return ResumoBronze(
        linhas=int(linhas),
        sha256=str(sha),
        chaves_duplicadas=int(dup),
        linhas_brutas=linhas_brutas,
        fora_do_dicionario=0,
    )


def resumir_parquet(con: duckdb.DuckDBPyConnection, glob: str, tabela: str) -> Resumo:
    """Tipa o Parquet pelo dicionário (qualquer que seja o tipo físico gravado) e resume."""
    origem = f"read_parquet({literal(glob)})"
    con.execute(f"CREATE OR REPLACE TEMP TABLE {tabela} AS {esquema.sql_tipado(origem)}")
    linhas, sha, dup = uma_linha(con, esquema.sql_resumo(tabela))
    return Resumo(int(linhas), str(sha), int(dup))


def _comparar(bronze: ResumoBronze | None, destino: Resumo | None, nome: str) -> list[str]:
    falhas: list[str] = []
    if bronze is None or destino is None:
        return falhas  # o erro de leitura já foi registrado
    if bronze.fora_do_dicionario:
        falhas.append(
            f"bronze: {_n(bronze.fora_do_dicionario, 'linha', 'linhas')} "
            "com valor fora do dicionário"
        )
    if bronze.linhas != bronze.linhas_brutas:
        falhas.append(
            f"bronze: o leitor viu {_n(bronze.linhas, 'linha', 'linhas')}, o arquivo "
            f"tem {bronze.linhas_brutas}"
        )
    if bronze.chaves_duplicadas:
        falhas.append(
            f"bronze: {_n(bronze.chaves_duplicadas, 'chave duplicada', 'chaves duplicadas')}"
        )
    if destino.chaves_duplicadas:
        falhas.append(
            f"{nome}: {_n(destino.chaves_duplicadas, 'chave duplicada', 'chaves duplicadas')}"
        )
    if bronze.linhas != destino.linhas:
        falhas.append(f"contagem: bronze {bronze.linhas} × {nome} {destino.linhas}")
    if bronze.sha256 and bronze.sha256 != destino.sha256:
        falhas.append(f"sha256: bronze {bronze.sha256[:12]}… × {nome} {destino.sha256[:12]}…")
    return falhas


def _fora_da_competencia(
    con: duckdb.DuckDBPyConnection, bronze: ResumoBronze | None, competencia: str
) -> list[str]:
    if bronze is None or not bronze.sha256:
        return []  # bronze não tipada: a falha já foi registrada
    fora = int(uma_linha(con, esquema.sql_fora_da_competencia("bronze", competencia))[0])
    return (
        [f"bronze: {_n(fora, 'linha', 'linhas')} com DT_COMPTC fora de {competencia}"]
        if fora
        else []
    )


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


def _registrar(arm: Armazenamento, caminho: str, dado: dict[str, Any], resposta: Resposta) -> None:
    """Grava o controle; se o lake recusar, a resposta vira ``ok: false`` com o motivo.

    Assim a Function sempre devolve o JSON que a guarda do pipeline lê: carga que não ficou
    registrada não segue.
    """
    try:
        _gravar_json(arm, caminho, dado)
    except OSError as erro:
        resposta["ok"] = False
        resposta["falhas"].append(
            f"controle não gravado ({caminho}): {type(erro).__name__}: {erro}"
        )


def validar(arm: Armazenamento, lake: Lake, competencia: str, carga: str) -> Resposta:
    """Passo 3: bronze × staging. ``ok`` libera o ADF para apagar e regravar a prata."""
    inicio = time.perf_counter()
    competencia, carga = conferir_competencia(competencia), conferir_carga(carga)
    falhas: list[str] = []
    bronze: ResumoBronze | None = None
    staging: Resumo | None = None
    try:
        with duckdb.connect(":memory:") as con:
            bronze = resumir_csv(
                con, csv_do_zip(arm.ler_bytes(lake.bronze_zip(competencia, carga)))
            )
            staging = resumir_parquet(
                con, arm.caminho_duckdb(parquets(lake.staging(competencia, carga))), "staging"
            )
            falhas += _fora_da_competencia(con, bronze, competencia)
    except (duckdb.Error, ErroDeLayout, OSError, ValueError, zipfile.BadZipFile) as erro:
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
    _registrar(arm, lake.relatorio_carga(competencia, carga), {"validacao": resposta}, resposta)
    return resposta


def reconciliar(
    arm: Armazenamento,
    lake: Lake,
    competencia: str,
    carga: str,
    origem: dict[str, Any] | None = None,
) -> Resposta:
    """Passo 6: bronze × prata, ouro refeito e conferido, manifesto gravado se tudo bater.

    ``origem`` traz os metadados HTTP da CVM (URL, ETag, Last-Modified): na Azure, do ``HEAD``
    que a entrada faz minutos depois da cópia; na máquina, do próprio download. O manifesto os
    guarda para a otimização futura da D3.
    """
    inicio = time.perf_counter()
    competencia, carga = conferir_competencia(competencia), conferir_carga(carga)
    falhas: list[str] = []
    bronze: ResumoBronze | None = None
    prata: Resumo | None = None
    conferencia_ouro: dict[str, Any] | None = None
    try:
        with duckdb.connect(":memory:") as con:
            bronze = resumir_csv(
                con, csv_do_zip(arm.ler_bytes(lake.bronze_zip(competencia, carga)))
            )
            prata = resumir_parquet(
                con, arm.caminho_duckdb(parquets(lake.prata(competencia))), "prata"
            )
            falhas += _fora_da_competencia(con, bronze, competencia)
            falhas += _comparar(bronze, prata, "prata")
            if not falhas:
                con.execute(f"CREATE OR REPLACE TEMP TABLE ouro AS {sql_ouro.SQL_OURO}")
                linha = uma_linha(con, sql_ouro.SQL_CONFERE_OURO)
                nomes = [d[0] for d in con.description or []]
                # Os decimais saem do DuckDB como Decimal com a escala do dicionário (2 casas):
                # str(Decimal) é exato e estável para comparar e gravar em JSON; int fica como está.
                conferencia_ouro = {
                    n: (str(v) if not isinstance(v, int) else v)
                    for n, v in zip(nomes, linha, strict=True)
                }
                for a, b in (
                    ("linhas_prata", "linhas_no_ouro"),
                    ("captacao_prata", "captacao_ouro"),
                    ("resgate_prata", "resgate_ouro"),
                ):
                    if conferencia_ouro[a] != conferencia_ouro[b]:
                        falhas.append(
                            f"ouro: {a} {conferencia_ouro[a]} × {b} {conferencia_ouro[b]}"
                        )
            if not falhas:
                with tempfile.TemporaryDirectory() as pasta:
                    arquivo = Path(pasta) / "ouro.parquet"
                    con.execute(f"COPY ouro TO {literal(str(arquivo))} (FORMAT parquet)")
                    arm.gravar_bytes(f"{lake.ouro(competencia)}/ouro.parquet", arquivo.read_bytes())
    except (duckdb.Error, ErroDeLayout, OSError, ValueError, zipfile.BadZipFile) as erro:
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
    try:
        relatorio = _ler_json(arm, caminho_relatorio)
    except (OSError, ValueError) as erro:  # ValueError: JSON corrompido
        relatorio = {"validacao_ilegivel": f"{type(erro).__name__}: {erro}"}
    relatorio["reconciliacao"] = resposta
    _registrar(arm, caminho_relatorio, relatorio, resposta)

    if resposta["ok"] and prata is not None:
        # O manifesto é a fonte de verdade do que está conferido: só muda com tudo gravado.
        _registrar(
            arm,
            lake.manifesto(competencia),
            {
                "competencia": competencia,
                "carga_vigente": carga,
                "linhas": prata.linhas,
                "sha256": prata.sha256,
                "origem": origem or {},
                "reconciliado_em": resposta["em"],
            },
            resposta,
        )
        if not resposta["ok"]:  # o relatório já dizia ok: regrava com o motivo
            relatorio["reconciliacao"] = resposta
            _registrar(arm, caminho_relatorio, relatorio, resposta)
    return resposta
