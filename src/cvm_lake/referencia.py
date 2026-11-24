"""Implementação de referência do hash canônico, em Python puro, sem DuckDB.

Serve para provar que a forma canônica (esquema.py, D7) é uma especificação, e não um detalhe
do DuckDB: outra ferramenta, seguindo as mesmas regras, chega ao mesmo SHA-256. A prova completa
e os testes cobram que as duas implementações concordem no mesmo arquivo.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from cvm_lake import esquema


def _escala(tipo: str) -> int | None:
    if tipo.startswith("DECIMAL("):
        return int(tipo.removesuffix(")").split(",")[1])
    return None


def sha256_canonico_csv(csv: bytes) -> tuple[int, str]:
    """(linhas, sha256) do CSV da CVM pelas regras da forma canônica."""
    linhas = csv.decode(esquema.CSV_ENCODING).split(esquema.CSV_FIM_DE_LINHA)
    if linhas and linhas[-1] == "":
        linhas.pop()
    escalas = [_escala(c.tipo) for c in esquema.COLUNAS]
    indice_chave = [esquema.NOMES.index(c) for c in esquema.CHAVE]

    canonicas: list[tuple[tuple[str, ...], str]] = []
    for linha in linhas[1:]:
        campos = linha.split(esquema.CSV_DELIMITADOR)
        saida = []
        for valor, coluna, escala in zip(campos, esquema.COLUNAS, escalas, strict=True):
            if valor == "":
                saida.append(esquema.NULO_CANONICO)
            elif escala is not None:
                # format "f": str(Decimal) sai em notação científica para zero ("0E-12").
                saida.append(format(Decimal(valor).quantize(Decimal(1).scaleb(-escala)), "f"))
            elif coluna.tipo == "INTEGER":
                saida.append(str(int(valor)))
            else:
                saida.append(valor)  # texto e data ISO já estão na forma canônica
        # Nulo da subclasse ordena primeiro: "" é menor que qualquer texto.
        chave = tuple(campos[i] for i in indice_chave)
        canonicas.append((chave, esquema.SEPARADOR_CANONICO.join(saida)))

    canonicas.sort(key=lambda par: par[0])
    corpo = "\n".join(texto for _, texto in canonicas)
    return len(canonicas), hashlib.sha256(corpo.encode("utf-8")).hexdigest()
