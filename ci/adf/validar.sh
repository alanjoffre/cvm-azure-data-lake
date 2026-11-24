#!/usr/bin/env bash
# Valida o JSON do ADF sem conta na Azure: validate e export do ARM pelo pacote da Microsoft
# (@microsoft/azure-data-factory-utilities) e os testes do repositório sobre o JSON e o ARM.
# O mesmo script roda na máquina e no CI.
#   ci/adf/validar.sh            # o ARM fica em saida/arm
set -euo pipefail
cd "$(dirname "$0")"
raiz_repo="$(cd ../.. && pwd)"
saida="$raiz_repo/saida/arm"
log="$raiz_repo/saida/adf-validar.log"
mkdir -p "$raiz_repo/saida"

# O pacote só usa o ID da fábrica como texto (no link da mensagem de erro e no nome do ARM): não
# fala com a Azure, mas precisa de internet para baixar o validador (o hash vai para o log). Assinatura zerada de propósito; o nome real entra no deploy por factoryName.
fabrica="/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake/providers/Microsoft.DataFactory/factories/adf-cvm-lake"

rodar() {
  if ! npm run --silent build -- "$@" >"$log" 2>&1; then
    grep -E "validation error|ERROR" "$log" | grep -v publish_config || tail -40 "$log"
    echo "Falhou: $1 (log completo em $log)"
    exit 1
  fi
}

echo "== npm ci"
npm ci --no-audit --no-fund --silent

echo "== validate"
rodar validate "$raiz_repo/adf" "$fabrica"
# O pacote baixa a cada execução o validador que o ADF Studio usa, sem versão: o hash registra
# qual bundle validou este JSON.
echo "bundle: $(sha256sum downloads/main.js | cut -c1-64)"

echo "== export do ARM"
rm -rf "$saida"
# A pasta de saída do export é sempre relativa à pasta atual (um caminho absoluto é concatenado).
rodar export "$raiz_repo/adf" "$fabrica" ../../saida/arm
ls "$saida"

echo "== testes do JSON e do ARM"
cd "$raiz_repo"
ADF_ARM="$saida" uv run pytest -q tests/test_adf.py

echo "ADF validado."
