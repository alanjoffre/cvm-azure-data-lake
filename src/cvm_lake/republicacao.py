"""Republicação simulada (D7): gera T1 a partir de T0, o mesmo mês "reapresentado".

A CVM não reapresenta um mês a pedido, então a prova fabrica a reapresentação de forma
determinística (semente fixa) e declarada: alguns valores alterados, uma linha removida e uma
incluída. A contagem de T1 é igual à de T0 de propósito: só o hash denuncia a diferença.

T1 sai no mesmo formato físico da CVM (``;``, CRLF, sem aspas, mesmo nome do CSV no ZIP), para
passar pelo pipeline exatamente como um arquivo real.
"""

from __future__ import annotations

import io
import random
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal

from cvm_lake import esquema
from cvm_lake.funcao.prova import csv_do_zip

_I = {nome: i for i, nome in enumerate(esquema.NOMES)}
Chave = tuple[str, str, str]


@dataclass(frozen=True)
class Mudancas:
    alteradas: list[Chave] = field(default_factory=list)
    removida: Chave | None = None
    incluida: Chave | None = None


def _chave(campos: list[str]) -> Chave:
    return (campos[_I["CNPJ_FUNDO_CLASSE"]], campos[_I["ID_SUBCLASSE"]], campos[_I["DT_COMPTC"]])


def _somar(texto: str, delta: str) -> str:
    """Soma em decimal exato, mantendo a escala do texto original (o formato da CVM)."""
    valor = Decimal(texto) + Decimal(delta)
    return format(valor.quantize(Decimal(texto)), "f")  # nunca notação científica


def gerar_t1(zip_t0: bytes, semente: int = 175, alterar: int = 3) -> tuple[bytes, Mudancas]:
    """T1 = T0 com ``alterar`` linhas reapresentadas, uma removida e uma incluída."""
    with zipfile.ZipFile(io.BytesIO(zip_t0)) as arquivo:
        nome_csv = arquivo.infolist()[0].filename
    texto = csv_do_zip(zip_t0).decode(esquema.CSV_ENCODING)
    linhas = texto.split(esquema.CSV_FIM_DE_LINHA)
    if linhas[-1] == "":
        linhas.pop()
    cabecalho, corpo = linhas[0], [linha.split(esquema.CSV_DELIMITADOR) for linha in linhas[1:]]
    if len(corpo) < alterar + 2:
        raise ValueError("arquivo pequeno demais para simular a republicação")

    sorteio = random.Random(semente)
    indices = sorteio.sample(range(len(corpo)), alterar + 1)
    a_alterar, a_remover = indices[:alterar], indices[alterar]

    # Reapresentação típica: o administrador corrige PL e captação de um dia.
    alteradas: list[Chave] = []
    for i in a_alterar:
        campos = corpo[i]
        campos[_I["VL_PATRIM_LIQ"]] = _somar(campos[_I["VL_PATRIM_LIQ"]], "1000.00")
        campos[_I["CAPTC_DIA"]] = _somar(campos[_I["CAPTC_DIA"]], "1000.00")
        alteradas.append(_chave(campos))

    removida = _chave(corpo[a_remover])

    # Linha incluída: um dia do mês que um fundo ainda não tinha informado (informe atrasado).
    dias = sorted({c[_I["DT_COMPTC"]] for c in corpo})
    existentes = {_chave(c) for c in corpo}
    incluida_campos: list[str] | None = None
    for i in sorteio.sample(range(len(corpo)), len(corpo)):
        base = corpo[i]
        for dia in dias:
            candidata = (base[_I["CNPJ_FUNDO_CLASSE"]], base[_I["ID_SUBCLASSE"]], dia)
            if candidata not in existentes:
                incluida_campos = list(base)
                incluida_campos[_I["DT_COMPTC"]] = dia
                break
        if incluida_campos is not None:
            break
    if incluida_campos is None:
        raise ValueError("nenhum fundo sem informe em algum dia do mês: não há o que incluir")

    novo_corpo = [c for j, c in enumerate(corpo) if j != a_remover] + [incluida_campos]
    novo_texto = esquema.CSV_FIM_DE_LINHA.join(
        [cabecalho, *(esquema.CSV_DELIMITADOR.join(c) for c in novo_corpo)]
    ) + esquema.CSV_FIM_DE_LINHA

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.writestr(nome_csv, novo_texto.encode(esquema.CSV_ENCODING))
    return saida.getvalue(), Mudancas(alteradas, removida, _chave(incluida_campos))
