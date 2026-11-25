"""O contrato HTTP da Function com a atividade Azure Function do ADF (docs/adf.md).

``POST /api/validar`` e ``POST /api/reconciliar``, corpo ``{"competencia": "AAAAMM", "carga":
"<RunId>"}``. Este módulo não importa o runtime da Azure: ``funcao_azure/function_app.py`` só
liga a requisição a ``tratar``, e os testes chamam ``tratar`` direto.

Status:
- 200 com a resposta de ``prova.validar``/``prova.reconciliar``, inclusive com ``ok: false``.
  Quem derruba a carga é a guarda Fail do pipeline, com as falhas na mensagem;
- 400 para corpo inválido: é defeito do pipeline, não do dado, e a atividade falha.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from cvm_lake.funcao import prova
from cvm_lake.funcao.armazenamento import Armazenamento
from cvm_lake.lake import Lake, conferir_carga, conferir_competencia

ETAPAS = ("validar", "reconciliar")
Cabecalhos = Callable[[str], dict[str, Any]]


def metadados_da_origem(
    cabecalhos: Cabecalhos, arm: Armazenamento, lake: Lake, competencia: str, carga: str
) -> dict[str, Any]:
    """O que o manifesto guarda da CVM, e se o tamanho servido ainda é o do ZIP da bronze.

    O ``HEAD`` acontece minutos depois do Copy. Se a CVM regravou o mês nesse meio-tempo, o
    ``ETag`` é de outra versão: ``confere_com_bronze`` falso diz que ele não descreve o que foi
    carregado, e a otimização futura da D3 não pode pular o mês por ele. A falha do ``HEAD`` não
    derruba a carga: o dado já está provado, e só a otimização fica sem o ``ETag``.
    """
    try:
        meta = cabecalhos(competencia)
    except OSError as erro:  # inclui URLError e tempo esgotado
        return {"erro": f"{type(erro).__name__}: {erro}"}
    try:
        bytes_bronze: int | None = arm.tamanho(lake.bronze_zip(competencia, carga))
    except OSError:
        bytes_bronze = None
    meta["bytes_bronze"] = bytes_bronze
    meta["confere_com_bronze"] = meta.get("bytes") is not None and meta["bytes"] == bytes_bronze
    return meta


def _pedido(corpo: bytes) -> tuple[str, str]:
    dado = json.loads(corpo)
    if not isinstance(dado, dict):
        raise ValueError("o corpo deve ser um objeto JSON")
    return conferir_competencia(dado["competencia"]), conferir_carga(dado["carga"])


def tratar(
    etapa: str, corpo: bytes, arm: Armazenamento, lake: Lake, cabecalhos: Cabecalhos
) -> tuple[int, dict[str, Any]]:
    """Valida o corpo e chama a etapa. Devolve (status HTTP, resposta): 400 para pedido
    inválido; 200 com o resultado da prova, inclusive quando ``ok`` é falso."""
    if etapa not in ETAPAS:
        raise ValueError(f"etapa desconhecida: {etapa!r}")
    try:
        competencia, carga = _pedido(corpo)
    except (ValueError, KeyError, TypeError) as erro:  # JSONDecodeError é ValueError
        return 400, {
            "etapa": etapa,
            "ok": False,
            "falhas": [f"pedido inválido: {type(erro).__name__}: {erro}"],
        }
    if etapa == "validar":
        return 200, prova.validar(arm, lake, competencia, carga)
    origem = metadados_da_origem(cabecalhos, arm, lake, competencia, carga)
    return 200, prova.reconciliar(arm, lake, competencia, carga, origem=origem)
