"""Layout do lake (docs/arquitetura.md, seção 4).

O mesmo layout vale para a pasta local e para o ADLS Gen2: só a raiz muda (``saida/lake`` ou o
endpoint dfs da conta, ``https://<conta>.dfs.core.windows.net``). Na Azure cada camada é um
container, o primeiro segmento depois da raiz (``funcao/adls.py``); aqui, uma subpasta. Os
caminhos são montados como texto, sem ``pathlib``, para servir aos dois.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_COMPETENCIA = re.compile(r"[0-9]{4}(0[1-9]|1[0-2])")  # [0-9]: \d aceitaria dígito Unicode
_CARGA = re.compile(r"[A-Za-z0-9_-]{1,64}")


def conferir_competencia(competencia: str) -> str:
    """AAAAMM válido, ou erro. Todo caminho passa por aqui antes de existir."""
    if not _COMPETENCIA.fullmatch(competencia):
        raise ValueError(f"competência inválida: {competencia!r} (esperado AAAAMM)")
    return competencia


def conferir_carga(carga: str) -> str:
    """Identificador de carga (o RunId do ADF é um GUID); nada que escape da pasta."""
    if not _CARGA.fullmatch(carga):
        raise ValueError(f"identificador de carga inválido: {carga!r}")
    return carga


@dataclass(frozen=True)
class Lake:
    raiz: str

    def _p(self, *partes: str) -> str:
        return "/".join([self.raiz.rstrip("/"), *partes])

    # bronze: o ZIP exatamente como veio, uma versão por carga (D5)
    def bronze_zip(self, competencia: str, carga: str) -> str:
        c, r = conferir_competencia(competencia), conferir_carga(carga)
        return self._p("bronze", "cvm", "inf_diario", f"competencia={c}", f"carga={r}",
                       f"inf_diario_fi_{c}.zip")

    def bronze_mes(self, competencia: str) -> str:
        c = conferir_competencia(competencia)
        return self._p("bronze", "cvm", "inf_diario", f"competencia={c}")

    # staging: o Parquet da carga, antes de validar (D6)
    def staging(self, competencia: str, carga: str) -> str:
        c, r = conferir_competencia(competencia), conferir_carga(carga)
        return self._p("staging", "cvm", "inf_diario", f"carga={r}", f"competencia={c}")

    def staging_parquet(self, competencia: str, carga: str) -> str:
        """O arquivo que o Copy grava na staging, com o nome fixo de adf/ (docs/adf.md)."""
        c = conferir_competencia(competencia)
        return f"{self.staging(c, carga)}/inf_diario_fi_{c}.parquet"

    # prata: uma partição por mês, substituída inteira (D6)
    def prata(self, competencia: str) -> str:
        c = conferir_competencia(competencia)
        return self._p("prata", "cvm", "inf_diario", f"competencia={c}")

    # ouro: indicadores por fundo/classe, subclasse e mês
    def ouro(self, competencia: str) -> str:
        c = conferir_competencia(competencia)
        return self._p("ouro", "cvm", "inf_diario_mensal", f"competencia={c}")

    # controle: manifesto (carga vigente) e relatório de cada carga, um arquivo por mês
    def controle(self, competencia: str) -> str:
        c = conferir_competencia(competencia)
        return self._p("controle", "cvm", "inf_diario", f"competencia={c}")

    def manifesto(self, competencia: str) -> str:
        return f"{self.controle(competencia)}/manifesto.json"

    def relatorio_carga(self, competencia: str, carga: str) -> str:
        return f"{self.controle(competencia)}/carga={conferir_carga(carga)}.json"


def parquets(pasta: str) -> str:
    """Quem lê uma camada lê a pasta inteira e não depende do nome do arquivo."""
    return f"{pasta}/*.parquet"
