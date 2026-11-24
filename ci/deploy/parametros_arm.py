"""Gera o arquivo de parâmetros do ARM do ADF a partir das saídas do Terraform do ambiente.

O ARM exportado (``ci/adf/validar.sh``) traz os endereços do ambiente com valores
``*.definido-no-deploy.invalid``, que nunca resolvem. O deploy troca cada um pela saída do
Terraform que o descreve: o JSON do ADF não conhece nome de conta, cofre ou Function. Um
parâmetro ``.invalid`` sem saída no ``MAPA`` falha aqui, antes de o ARM chegar à Azure.

    terraform -chdir=infra output -json > saidas.json
    python3 ci/deploy/parametros_arm.py saidas.json \\
        saida/arm/ARMTemplateParametersForFactory.json > parametros.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# parâmetro do ARM → saída de infra/outputs.tf
MAPA = {
    "factoryName": "adf_nome",
    "ls_adls_lake_properties_typeProperties_url": "lake_dfs_endpoint",
    "ls_kv_properties_typeProperties_baseUrl": "key_vault_uri",
    "ls_funcao_properties_typeProperties_functionAppUrl": "funcao_host",
}
MARCA = ".invalid"


def gerar(saidas: dict[str, Any], parametros: dict[str, Any]) -> dict[str, Any]:
    valores = {nome: dado["value"] for nome, dado in parametros["parameters"].items()}
    faltam = sorted(n for n, v in valores.items() if MARCA in str(v) and n not in MAPA)
    if faltam:
        raise ValueError(f"parâmetros do ARM sem saída do Terraform: {faltam}")
    for parametro, saida in MAPA.items():
        if parametro not in valores:
            raise ValueError(f"o ARM não tem o parâmetro {parametro}")
        if saida not in saidas:
            raise ValueError(f"o Terraform não devolveu a saída {saida}")
        valor = saidas[saida]["value"]
        if not isinstance(valor, str) or not valor or MARCA in valor:
            raise ValueError(f"saída {saida} vazia ou de exemplo: {valor!r}")
        valores[parametro] = valor
    return {"$schema": parametros["$schema"], "contentVersion": parametros["contentVersion"],
            "parameters": {n: {"value": v} for n, v in valores.items()}}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    saidas = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    parametros = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    json.dump(gerar(saidas, parametros), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
