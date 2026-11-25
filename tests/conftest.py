"""Dado sintético no formato físico da CVM: ``;``, CRLF, ASCII, sem aspas, um CSV por ZIP.

Pequeno, determinístico e com os casos que o arquivo real tem: subclasse vazia e preenchida,
cota zero (que expôs o ``0E-12`` na referência), valores negativos, cota com zeros à direita
(o double grava 1.5; a forma canônica devolve as 12 casas e o hash não muda) e um fundo que
deixou de informar um dia (para a republicação incluir).
"""

from __future__ import annotations

import calendar
import io
import random
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from cvm_lake import esquema
from cvm_lake.origem import nome_arquivo

COMPETENCIAS = ["202609", "202610", "202611"]


def dias_uteis(competencia: str, quantos: int = 5) -> list[str]:
    ano, mes = int(competencia[:4]), int(competencia[4:])
    dias = [date(ano, mes, d) for d in range(1, calendar.monthrange(ano, mes)[1] + 1)]
    return [d.isoformat() for d in dias if d.weekday() < 5][:quantos]


def linhas_csv(competencia: str, fundos: int = 12, semente: int = 7) -> list[list[str]]:
    sorteio = random.Random(f"{semente}-{competencia}")
    linhas: list[list[str]] = []
    for f in range(fundos):
        cnpj = f"{10 + f:02d}.{f:03d}.{f * 7 % 1000:03d}/0001-{f % 100:02d}"
        subclasses = ["", f"SUB{f:03d}"] if f % 4 == 0 else [""]
        tp = ("CLASSES - FIF", "CLASSE FIF/FAPI", "FI")[f % 3]
        for subclasse in subclasses:
            for i, dia in enumerate(dias_uteis(competencia)):
                if f == 1 and i == 2:
                    continue  # informe que faltou: a republicação tem onde incluir
                if f == 2:
                    cota = "0.000000000000"
                elif f == 3:
                    cota = f"{1 + i / 2:.12f}"  # 1.500000000000: o double grava "1.5"
                else:
                    cota = f"{sorteio.uniform(0.5, 9):.12f}"
                total = sorteio.uniform(-5000, 9e6) if f == 5 else sorteio.uniform(0, 9e6)
                linhas.append(
                    [
                        tp,
                        cnpj,
                        subclasse,
                        dia,
                        f"{total:.2f}",
                        cota,
                        f"{sorteio.uniform(1e3, 9e6):.2f}",
                        f"{sorteio.uniform(0, 5e4):.2f}",
                        f"{sorteio.uniform(0, 5e4):.2f}",
                        str(sorteio.randint(0, 4000)),
                    ]
                )
    return linhas


def montar_csv(linhas: list[list[str]], cabecalho: list[str] | None = None) -> bytes:
    texto = [esquema.CSV_DELIMITADOR.join(cabecalho or esquema.NOMES)]
    texto += [esquema.CSV_DELIMITADOR.join(linha) for linha in linhas]
    return (esquema.CSV_FIM_DE_LINHA.join(texto) + esquema.CSV_FIM_DE_LINHA).encode("ascii")


def montar_zip(csv: bytes, competencia: str) -> bytes:
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", compression=zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.writestr(f"inf_diario_fi_{competencia}.csv", csv)
    return saida.getvalue()


def zip_sintetico(competencia: str, **kwargs: int) -> bytes:
    return montar_zip(montar_csv(linhas_csv(competencia, **kwargs)), competencia)


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    """Pasta no formato da ``OrigemPasta`` com os três meses sintéticos."""
    pasta = tmp_path / "origem_T0"
    pasta.mkdir()
    for c in COMPETENCIAS:
        (pasta / nome_arquivo(c)).write_bytes(zip_sintetico(c))
    return pasta


@pytest.fixture
def gravar_snapshot(tmp_path: Path) -> Callable[[dict[str, bytes]], Path]:
    """Monta uma pasta de origem com ZIPs feitos sob medida pelo teste."""

    def gravar(zips: dict[str, bytes]) -> Path:
        pasta = tmp_path / "origem_sob_medida"
        pasta.mkdir(exist_ok=True)
        for c, conteudo in zips.items():
            (pasta / nome_arquivo(c)).write_bytes(conteudo)
        return pasta

    return gravar
