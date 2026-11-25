"""Entrada da Azure Function (modelo de programação v2 do Python).

Só liga a requisição HTTP ao contrato de ``cvm_lake.funcao.entrada``. A rota é o
``functionName`` das atividades do ADF (``adf/pipeline/pl_inf_diario_mes.json``). O nível de
autorização é ``FUNCTION``; o ADF manda a chave de host ``adf`` (host key, do tipo
``functionKeys``), que vem do Key Vault (segredo ``funcao-chave-adf``, gravado pelo deploy).

App settings lidos (gravados pelo Terraform, ``infra/modules/ambiente/funcao.tf``):
``LAKE_CONTA``, a conta do ADLS Gen2.
"""

from __future__ import annotations

import json
import os

import azure.functions as func

from cvm_lake.funcao.adls import ArmazenamentoADLS
from cvm_lake.funcao.entrada import tratar
from cvm_lake.lake import Lake
from cvm_lake.origem import OrigemHTTP

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

# O HEAD na CVM tem de caber com folga nos 230 s que a Function tem para responder por HTTP.
TIMEOUT_CVM_S = 20.0


def responder(etapa: str, req: func.HttpRequest) -> func.HttpResponse:
    with ArmazenamentoADLS.da_conta(os.environ["LAKE_CONTA"]) as arm:
        status, corpo = tratar(
            etapa, req.get_body(), arm, Lake(arm.raiz), OrigemHTTP(timeout=TIMEOUT_CVM_S).cabecalhos
        )
    return func.HttpResponse(
        json.dumps(corpo, ensure_ascii=False),
        status_code=status,
        mimetype="application/json",
        charset="utf-8",
    )


@app.route(route="validar", methods=["POST"])
def validar(req: func.HttpRequest) -> func.HttpResponse:
    return responder("validar", req)


@app.route(route="reconciliar", methods=["POST"])
def reconciliar(req: func.HttpRequest) -> func.HttpResponse:
    return responder("reconciliar", req)
