"""Janela de reprocessamento (D3): quais competências cada gatilho recarrega.

O contrato da CVM: M e M-1 mudam todo dia; M-2 a M-11 mudam uma vez por semana; o resto não
muda mais. O ADF calcula a mesma lista por expressão no pipeline pai; este módulo é a versão
testável dela, e os testes fixam os casos de virada de ano.
"""

from __future__ import annotations

from datetime import date

JANELA_DIARIA = (0, 1)  # M e M-1
JANELA_SEMANAL = (2, 11)  # M-2 a M-11


def competencia_menos(referencia: date, meses: int) -> str:
    """AAAAMM do mês ``meses`` antes do mês de ``referencia``."""
    if meses < 0:
        raise ValueError("meses precisa ser >= 0")
    indice = referencia.year * 12 + (referencia.month - 1) - meses
    return f"{indice // 12:04d}{indice % 12 + 1:02d}"


def janela(referencia: date, de: int, ate: int) -> list[str]:
    """Competências de M-``de`` a M-``ate``, da mais recente para a mais antiga."""
    if not 0 <= de <= ate:
        raise ValueError("esperado 0 <= de <= ate")
    return [competencia_menos(referencia, n) for n in range(de, ate + 1)]
