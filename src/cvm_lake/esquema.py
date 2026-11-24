"""Dicionário do Informe Diário e a forma canônica usada no hash (D4, D7).

Tudo que define "o mesmo dado" mora aqui: os tipos do dicionário da CVM, a chave natural e o
texto canônico de cada linha. A bronze (CSV) e a prata (Parquet) passam pelo mesmo SQL, então o
hash não depende do formato do arquivo, só do conteúdo.

Fonte dos tipos: https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/META/meta_inf_diario_fi.txt
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Coluna:
    nome: str
    tipo: str  # tipo DuckDB equivalente ao do dicionário da CVM
    # Regex que o texto do CSV precisa casar por inteiro. Serve para recusar um valor que o CAST
    # aceitaria arredondando (ex.: 3 casas num campo de escala 2), o que esconderia uma perda.
    # Nos campos de texto, "|" e "\" ficam proibidos: são o separador e o nulo da forma
    # canônica, e permiti-los tornaria duas linhas diferentes iguais no hash.
    padrao: str


def _numerico(precisao: int, escala: int) -> str:
    inteiros = precisao - escala
    if escala == 0:
        return rf"-?\d{{1,{inteiros}}}"
    return rf"-?\d{{1,{inteiros}}}(\.\d{{1,{escala}}})?"


# Na ordem do arquivo. Tamanhos e escalas copiados do dicionário (conferido em 24/11/2026).
COLUNAS: tuple[Coluna, ...] = (
    Coluna("TP_FUNDO_CLASSE", "VARCHAR", r"[^|\\]{1,20}"),
    Coluna("CNPJ_FUNDO_CLASSE", "VARCHAR", r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}"),
    Coluna("ID_SUBCLASSE", "VARCHAR", r"[^|\\]{0,15}"),
    Coluna("DT_COMPTC", "DATE", r"\d{4}-\d{2}-\d{2}"),
    Coluna("VL_TOTAL", "DECIMAL(17,2)", _numerico(17, 2)),
    Coluna("VL_QUOTA", "DECIMAL(27,12)", _numerico(27, 12)),
    Coluna("VL_PATRIM_LIQ", "DECIMAL(17,2)", _numerico(17, 2)),
    Coluna("CAPTC_DIA", "DECIMAL(17,2)", _numerico(17, 2)),
    Coluna("RESG_DIA", "DECIMAL(17,2)", _numerico(17, 2)),
    Coluna("NR_COTST", "INTEGER", _numerico(10, 0)),
)

NOMES: tuple[str, ...] = tuple(c.nome for c in COLUNAS)

# D4: desde a Resolução CVM 175 a subclasse faz parte da chave. Subclasse vazia é um valor.
CHAVE: tuple[str, ...] = ("CNPJ_FUNDO_CLASSE", "ID_SUBCLASSE", "DT_COMPTC")

# Formato físico do CSV da CVM (conferido em 24/11/2026 nos meses 2026-09 a 2026-11).
CSV_DELIMITADOR = ";"
CSV_FIM_DE_LINHA = "\r\n"
CSV_ENCODING = "ascii"  # os 3 meses não têm nenhum byte acima de 127; o dicionário vem em latin-1

SEPARADOR_CANONICO = "|"
NULO_CANONICO = "\\N"


def _q(nome: str) -> str:
    return f'"{nome}"'


def sql_tipado(origem: str) -> str:
    """SELECT que converte qualquer origem (CSV em texto ou Parquet) para os tipos do dicionário.

    ``origem`` é uma expressão de tabela do DuckDB (``read_csv(...)``, ``read_parquet(...)``).
    O CAST falha alto se um valor não couber no tipo: preferimos a carga recusada a um número
    truncado em silêncio. Texto vazio vira nulo nas colunas de texto, como o ADF faz por padrão
    (``treatEmptyAsNull``); a forma canônica trata os dois do mesmo jeito.
    """
    partes = []
    for c in COLUNAS:
        if c.tipo == "VARCHAR":
            partes.append(f"NULLIF(CAST({_q(c.nome)} AS VARCHAR), '') AS {_q(c.nome)}")
        else:
            partes.append(f"CAST({_q(c.nome)} AS {c.tipo}) AS {_q(c.nome)}")
    return f"SELECT {', '.join(partes)} FROM {origem}"


def sql_linha_canonica(alias: str = "t") -> str:
    """Expressão que monta o texto canônico de uma linha já tipada.

    Regras (D7), para que qualquer ferramenta recalcule o mesmo hash:
    - campos na ordem do dicionário, separados por ``|``;
    - decimal com exatamente a escala do dicionário (``0.00``, ``44.636723400000``);
    - data em ISO ``AAAA-MM-DD``; inteiro sem zeros à esquerda;
    - nulo ou texto vazio como ``\\N``.
    """
    campos = []
    for c in COLUNAS:
        col = f"{alias}.{_q(c.nome)}"
        # DECIMAL(p,s) → VARCHAR no DuckDB sai sempre com s casas; INTEGER sai sem zeros.
        texto = (f"strftime({col}, '%Y-%m-%d')" if c.tipo == "DATE"
                 else f"CAST({col} AS VARCHAR)")
        campos.append(f"COALESCE(NULLIF({texto}, ''), '{NULO_CANONICO}')")
    return f" || '{SEPARADOR_CANONICO}' || ".join(campos)


def sql_ordem_chave(alias: str = "t") -> str:
    """ORDER BY total e determinístico pela chave natural (nulo da subclasse vem primeiro)."""
    return ", ".join(f"{alias}.{_q(c)} ASC NULLS FIRST" for c in CHAVE)


def sql_resumo(tabela_tipada: str) -> str:
    """Contagem, SHA-256 canônico e duplicidade de chave de uma tabela já tipada.

    O hash é o SHA-256 (hex) da concatenação das linhas canônicas, ordenadas pela chave e
    separadas por ``\\n``, sem ``\\n`` no fim. Ordenar pela chave torna o hash independente da
    ordem física das linhas no arquivo.
    """
    chave = ", ".join(_q(c) for c in CHAVE)
    return f"""
        SELECT
            (SELECT count(*) FROM {tabela_tipada}) AS linhas,
            (SELECT coalesce(sha256(string_agg({sql_linha_canonica()}, chr(10)
                                               ORDER BY {sql_ordem_chave()})), '')
               FROM {tabela_tipada} AS t) AS sha256,
            (SELECT coalesce(sum(n - 1), 0) FROM (
                SELECT count(*) AS n FROM {tabela_tipada} GROUP BY {chave} HAVING count(*) > 1
            )) AS chaves_duplicadas
    """


def sql_fora_do_dicionario(tabela_texto: str) -> str:
    """Conta, no CSV ainda em texto, os valores que não casam com o padrão do dicionário."""
    condicoes = []
    for c in COLUNAS:
        col = f"coalesce({_q(c.nome)}, '')"
        condicoes.append(f"NOT regexp_full_match({col}, '{c.padrao}')")
    return f"SELECT count(*) FROM {tabela_texto} WHERE {' OR '.join(condicoes)}"
