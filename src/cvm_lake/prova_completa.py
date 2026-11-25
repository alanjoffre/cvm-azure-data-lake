"""A prova do desenho (docs/testes.md): roda igual com dado sintético (pytest) e real (CLI).

Sequência, sobre um lake vazio e uma pasta de snapshot T0 com os meses da janela:

1. carga inicial (T0) de todos os meses;
2. reexecução com a mesma origem: idempotência (mesmos hashes, uma versão nova na bronze);
3. republicação simulada de um mês (T1, ``republicacao.gerar_t1``): só esse mês muda, a prata
   fica igual a T1, a contagem igual à de T0 e o hash diferente;
4. divergência injetada na staging entre o Copy e a validação: a validação falha, a prata e o
   manifesto continuam os da carga anterior;
5. o hash de referência em Python puro (``referencia.py``) é igual ao do DuckDB em todo arquivo.

Cada passo vira uma lista de checagens (nome, ok, detalhe). A prova passa se todas passarem.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from cvm_lake import adf_local, esquema
from cvm_lake.funcao.prova import csv_do_zip, literal, uma_linha
from cvm_lake.lake import Lake, parquets
from cvm_lake.origem import Arquivo, OrigemPasta, guardar, metadados_de, nome_arquivo
from cvm_lake.referencia import sha256_canonico_csv
from cvm_lake.republicacao import Chave, gerar_t1

Relatorio = dict[str, Any]


@dataclass(frozen=True)
class Checagem:
    nome: str
    ok: bool
    detalhe: str


def mil(n: int) -> str:
    """Inteiro com ponto de milhar, como se escreve em pt-BR (538.758)."""
    return f"{n:,}".replace(",", ".")


def _ler_json(caminho: str) -> dict[str, Any]:
    dado: dict[str, Any] = json.loads(Path(caminho).read_text(encoding="utf-8"))
    return dado


def _sha_tabela(glob: str) -> str:
    """Hash de uma tabela inteira, independente da ordem física (para comparar o ouro)."""
    sql = f"""SELECT sha256(coalesce(string_agg(CAST(t AS VARCHAR), chr(10)
                                                ORDER BY CAST(t AS VARCHAR)), ''))
              FROM read_parquet({literal(glob)}) AS t"""
    with duckdb.connect(":memory:") as con:
        return str(uma_linha(con, sql)[0])


def _versoes_bronze(lake: Lake, competencia: str) -> int:
    return len(list(Path(lake.bronze_mes(competencia)).glob("carga=*")))


def _perfil(lake: Lake, competencia: str, zip_t0: bytes, csv_t0: bytes) -> dict[str, Any]:
    """O que o arquivo do mês tem, medido na prata (já tipada pelo dicionário)."""
    origem = f"read_parquet({literal(parquets(lake.prata(competencia)))})"
    with duckdb.connect(":memory:") as con:
        linha = uma_linha(
            con,
            f"""
            SELECT count(*), count(DISTINCT "CNPJ_FUNDO_CLASSE"),
                   count(DISTINCT ("CNPJ_FUNDO_CLASSE", coalesce("ID_SUBCLASSE", ''))),
                   count(DISTINCT "DT_COMPTC"), min("DT_COMPTC"), max("DT_COMPTC"),
                   count(*) FILTER (WHERE "ID_SUBCLASSE" IS NULL)
            FROM {origem}""",
        )
    nomes = [
        "linhas",
        "fundos",
        "pares_fundo_subclasse",
        "datas",
        "primeira_data",
        "ultima_data",
        "linhas_sem_subclasse",
    ]
    perfil = {
        n: (v.isoformat() if hasattr(v, "isoformat") else int(v))
        for n, v in zip(nomes, linha, strict=True)
    }
    perfil["bytes_zip"] = len(zip_t0)
    perfil["bytes_csv"] = len(csv_t0)
    return perfil


def _valores(lake: Lake, competencia: str, chaves: list[Chave]) -> dict[Chave, tuple[str, str]]:
    """(VL_PATRIM_LIQ, CAPTC_DIA) das chaves pedidas, como estão na prata."""
    if not chaves:
        return {}
    lista = ", ".join(f"({literal(c)}, {literal(s)}, {literal(d)})" for c, s, d in chaves)
    with duckdb.connect(":memory:") as con:
        linhas = con.execute(f"""
            SELECT p."CNPJ_FUNDO_CLASSE", coalesce(p."ID_SUBCLASSE", ''),
                   strftime(p."DT_COMPTC", '%Y-%m-%d'),
                   CAST(p."VL_PATRIM_LIQ" AS VARCHAR), CAST(p."CAPTC_DIA" AS VARCHAR)
            FROM read_parquet({literal(parquets(lake.prata(competencia)))}) AS p
            JOIN (VALUES {lista}) AS k(c, s, d)
              ON p."CNPJ_FUNDO_CLASSE" = k.c AND coalesce(p."ID_SUBCLASSE", '') = k.s
             AND strftime(p."DT_COMPTC", '%Y-%m-%d') = k.d""").fetchall()
    return {(c, s, d): (v, cap) for c, s, d, v, cap in linhas}


def _mais_mil(texto: str) -> str:
    return format(Decimal(texto) + Decimal("1000.00"), "f")


def corromper_staging(lake: Lake, competencia: str, carga: str) -> None:
    """Gancho da prova: soma R$ 0,01 ao PL de uma linha da staging, sem mudar a contagem.

    É o defeito que só o hash pega: mesmo número de linhas, mesmas chaves, um centavo a mais.
    """
    arquivo = Path(lake.staging_parquet(competencia, carga))
    with duckdb.connect(":memory:") as con:
        con.execute(f"""CREATE TABLE s AS SELECT *, row_number() OVER () AS n
                        FROM read_parquet({literal(str(arquivo))})""")
        con.execute(f"""
            COPY (SELECT * EXCLUDE (n) REPLACE (
                      CASE WHEN n = 1
                           THEN CAST(coalesce("VL_PATRIM_LIQ", 0) + 0.01 AS DECIMAL(17,2))
                           ELSE "VL_PATRIM_LIQ" END AS "VL_PATRIM_LIQ")
                  FROM s ORDER BY n)
            TO {literal(str(arquivo))} (FORMAT parquet)""")


def _sha_prata(lake: Lake, competencia: str) -> str:
    """SHA-256 canônico da prata vigente de um mês."""
    origem = f"read_parquet({literal(parquets(lake.prata(competencia)))})"
    with duckdb.connect(":memory:") as con:
        con.execute(f"CREATE TEMP TABLE prata AS {esquema.sql_tipado(origem)}")
        return str(uma_linha(con, esquema.sql_resumo("prata"))[1])


def _tempos(rotulo: str, respostas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "carga": rotulo,
            "competencia": r["competencia"],
            "pipeline_s": r["segundos"],
            "validar_s": r["validacao"]["segundos"],
            "reconciliar_s": r["reconciliacao"]["segundos"],
            "funcao_s": round(r["validacao"]["segundos"] + r["reconciliacao"]["segundos"], 3),
        }
        for r in respostas
    ]


def executar(
    pasta_t0: Path, competencias: list[str], trabalho: Path, republicar: str | None = None
) -> Relatorio:
    """Roda a prova inteira. ``trabalho`` precisa estar vazio: o lake nasce do zero."""
    if trabalho.exists() and any(trabalho.iterdir()):
        raise ValueError(f"{trabalho} não está vazio: a prova começa de um lake vazio")
    republicar = republicar or competencias[len(competencias) // 2]
    if republicar not in competencias:
        raise ValueError(f"{republicar} não está entre as competências da prova")
    lake = Lake(str(trabalho / "lake"))
    checagens: list[Checagem] = []

    def checar(nome: str, ok: bool, detalhe: str) -> None:
        checagens.append(Checagem(nome, bool(ok), detalhe))

    origem_t0 = OrigemPasta(pasta_t0)
    zips_t0 = {c: origem_t0.baixar(c) for c in competencias}
    csvs_t0 = {c: csv_do_zip(a.conteudo) for c, a in zips_t0.items()}
    ref_t0 = {c: sha256_canonico_csv(csv) for c, csv in csvs_t0.items()}

    # 1. Carga inicial ---------------------------------------------------------------------------
    carga_t0 = adf_local.novo_run_id()
    r_t0 = adf_local.pl_inf_diario(origem_t0, lake, competencias, carga_t0)
    man_t0 = {c: _ler_json(lake.manifesto(c)) for c in competencias}
    ouro_t0 = {c: _sha_tabela(parquets(lake.ouro(c))) for c in competencias}
    perfis = {c: _perfil(lake, c, zips_t0[c].conteudo, csvs_t0[c]) for c in competencias}
    for c in competencias:
        m, (ref_linhas, ref_sha) = man_t0[c], ref_t0[c]
        checar(
            f"T0 {c}: manifesto aponta a carga", m["carga_vigente"] == carga_t0, m["carga_vigente"]
        )
        checar(
            f"T0 {c}: referência Python = DuckDB",
            (ref_linhas, ref_sha) == (m["linhas"], m["sha256"]),
            f"{mil(ref_linhas)} linhas, {ref_sha[:16]}… × {mil(m['linhas'])}, {m['sha256'][:16]}…",
        )
        checar(
            f"T0 {c}: staging apagada no fim",
            not Path(lake.staging(c, carga_t0)).exists(),
            f"carga={carga_t0}",
        )

    # 2. Reexecução: idempotência --------------------------------------------------------------
    carga_re = adf_local.novo_run_id()
    r_re = adf_local.pl_inf_diario(origem_t0, lake, competencias, carga_re)
    for c in competencias:
        m = _ler_json(lake.manifesto(c))
        checar(
            f"reexecução {c}: mesmo hash e mesma contagem",
            (m["linhas"], m["sha256"]) == (man_t0[c]["linhas"], man_t0[c]["sha256"]),
            f"{mil(m['linhas'])} linhas, {m['sha256'][:16]}…",
        )
        checar(
            f"reexecução {c}: ouro idêntico",
            _sha_tabela(parquets(lake.ouro(c))) == ouro_t0[c],
            ouro_t0[c][:16] + "…",
        )
        checar(
            f"reexecução {c}: 2 versões na bronze, manifesto na nova",
            _versoes_bronze(lake, c) == 2 and m["carga_vigente"] == carga_re,
            f"{_versoes_bronze(lake, c)} versões; vigente {m['carga_vigente']}",
        )

    # 3. Republicação simulada de um mês -----------------------------------------------------------
    zip_t1, mudancas = gerar_t1(zips_t0[republicar].conteudo)
    csv_t1 = csv_do_zip(zip_t1)
    ref_t1 = sha256_canonico_csv(csv_t1)
    pasta_t1 = trabalho / "origem_T1"
    pasta_t1.mkdir(parents=True)
    for c in competencias:
        if c == republicar:
            meta = metadados_de(zips_t0[c].metadados.url + "#T1-simulado", zip_t1, None, None)
            guardar(Arquivo(zip_t1, meta), pasta_t1, c)
        else:
            shutil.copy2(pasta_t0 / nome_arquivo(c), pasta_t1 / nome_arquivo(c))
            lateral = (pasta_t0 / nome_arquivo(c)).with_suffix(".origem.json")
            if lateral.exists():
                shutil.copy2(lateral, (pasta_t1 / nome_arquivo(c)).with_suffix(".origem.json"))
    antes = _valores(lake, republicar, [*mudancas.alteradas, *filter(None, [mudancas.removida])])

    carga_t1 = adf_local.novo_run_id()
    r_t1 = adf_local.pl_inf_diario(OrigemPasta(pasta_t1), lake, competencias, carga_t1)
    for c in competencias:
        m = _ler_json(lake.manifesto(c))
        if c == republicar:
            checar(
                f"T1 {c}: prata = T1 (hash calculado à parte)",
                (m["linhas"], m["sha256"]) == ref_t1,
                f"{mil(m['linhas'])} linhas, {m['sha256'][:16]}…",
            )
            checar(
                f"T1 {c}: contagem igual à de T0 e hash diferente",
                m["linhas"] == man_t0[c]["linhas"] and m["sha256"] != man_t0[c]["sha256"],
                f"{mil(man_t0[c]['linhas'])} = {mil(m['linhas'])}; "
                f"{man_t0[c]['sha256'][:12]}… ≠ {m['sha256'][:12]}…",
            )
            checar(
                f"T1 {c}: ouro refeito",
                _sha_tabela(parquets(lake.ouro(c))) != ouro_t0[c],
                "hash do ouro mudou",
            )
        else:
            checar(
                f"T1 {c}: mês não republicado ficou igual",
                m["sha256"] == man_t0[c]["sha256"]
                and _sha_tabela(parquets(lake.ouro(c))) == ouro_t0[c],
                m["sha256"][:16] + "…",
            )
    depois = _valores(
        lake,
        republicar,
        [*mudancas.alteradas, *filter(None, [mudancas.removida, mudancas.incluida])],
    )
    checar(
        f"T1 {republicar}: linha removida ausente da prata",
        mudancas.removida in antes and mudancas.removida not in depois,
        str(mudancas.removida),
    )
    checar(
        f"T1 {republicar}: linha incluída presente na prata",
        mudancas.incluida in depois,
        str(mudancas.incluida),
    )
    for chave in mudancas.alteradas:
        esperado = tuple(_mais_mil(v) for v in antes[chave])
        checar(
            f"T1 {republicar}: linha alterada com o valor novo",
            depois.get(chave) == esperado,
            f"{chave}: {antes[chave]} → {depois.get(chave)}",
        )

    # 4. Divergência injetada na staging -------------------------------------------------------
    carga_div = adf_local.novo_run_id()
    falha: dict[str, Any] | None = None
    try:
        adf_local.pl_inf_diario_mes(
            OrigemPasta(pasta_t1), lake, republicar, carga_div, depois_da_staging=corromper_staging
        )
    except adf_local.CargaFalhou as erro:
        falha = erro.resposta
    m = _ler_json(lake.manifesto(republicar))
    checar(
        f"divergência {republicar}: a validação falhou pelo hash",
        falha is not None
        and falha["etapa"] == "validar"
        and any(f.startswith("sha256") for f in falha["falhas"])
        and not any(f.startswith("contagem") for f in falha["falhas"]),
        "; ".join(falha["falhas"]) if falha else "a carga passou (não deveria)",
    )
    checar(
        f"divergência {republicar}: manifesto continua na carga T1",
        m["carga_vigente"] == carga_t1 and m["sha256"] == ref_t1[1],
        m["carga_vigente"],
    )
    prata_hoje = _sha_prata(lake, republicar)
    checar(
        f"divergência {republicar}: prata intacta (= T1)",
        prata_hoje == ref_t1[1],
        prata_hoje[:16] + "…",
    )
    checar(
        f"divergência {republicar}: staging preservada para diagnóstico",
        Path(lake.staging(republicar, carga_div)).exists(),
        f"carga={carga_div}",
    )

    # 5. Tempos --------------------------------------------------------------------------------
    tempos = [*_tempos("T0", r_t0), *_tempos("reexecução", r_re), *_tempos("T1", r_t1)]

    return {
        "gerado_em": datetime.now(UTC).isoformat(timespec="seconds"),
        "ok": all(c.ok for c in checagens),
        "competencias": competencias,
        "republicada": republicar,
        "cargas": {
            "T0": carga_t0,
            "reexecucao": carga_re,
            "T1": carga_t1,
            "divergencia": carga_div,
        },
        "meses": {
            c: {
                "origem": asdict(zips_t0[c].metadados),
                "perfil": perfis[c],
                "linhas_t0": man_t0[c]["linhas"],
                "sha256_t0": man_t0[c]["sha256"],
                "sha256_referencia_t0": ref_t0[c][1],
            }
            for c in competencias
        },
        "republicacao": {
            "competencia": republicar,
            "sha256_t1": ref_t1[1],
            "linhas_t1": ref_t1[0],
            "alteradas": [list(k) for k in mudancas.alteradas],
            "removida": list(mudancas.removida or ()),
            "incluida": list(mudancas.incluida or ()),
        },
        "tempos": tempos,
        "funcao_s_max": max(t["funcao_s"] for t in tempos),
        "checagens": [asdict(c) for c in checagens],
    }
