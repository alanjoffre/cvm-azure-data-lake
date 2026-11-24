"""Publica como variáveis do Azure Pipelines as saídas do Terraform que o deploy usa.

    python3 ci/deploy/variaveis.py saidas.json

Imprime ``##vso[task.setvariable variable=<nome>]<valor>`` para cada nome de ``NOMES``: os passos
seguintes do job leem ``$(adf_nome)``, ``$(funcao_nome)`` etc. Nenhuma saída é segredo.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

NOMES = ("resource_group_name", "adf_nome", "funcao_nome", "key_vault_nome")


def linhas(saidas: dict[str, Any]) -> list[str]:
    resultado = []
    for nome in NOMES:
        valor = saidas[nome]["value"]
        if not isinstance(valor, str) or not valor or any(c in valor for c in "\r\n;]"):
            raise ValueError(f"saída {nome} inválida: {valor!r}")
        resultado.append(f"##vso[task.setvariable variable={nome}]{valor}")
    return resultado


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    print("\n".join(linhas(json.loads(Path(argv[0]).read_text(encoding="utf-8")))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
