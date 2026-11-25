#!/usr/bin/env bash
# Publica a Function e entrega a chave dela ao ADF pelo cofre, sem o valor passar por argumento,
# log ou estado do Terraform (keyvault.tf). Roda antes do ARM do ADF: o linked service da
# Function lê o segredo funcao-chave-adf no cofre.
#   ci/deploy/funcao.sh <resource-group> <function-app> <key-vault> <funcao.zip>
# Precisa da CLI já autenticada (AzureCLI@2 com a service connection por federação).
set -euo pipefail
[[ $# -eq 4 ]] || { sed -n 2,6p "$0" >&2; exit 2; }
rg="$1" funcao="$2" cofre="$3" pacote="$4"
segredo="funcao-chave-adf"
chave="adf"

echo "== publicar $pacote em $funcao"
# Flex Consumption em Python: zip da raiz do projeto e build remoto sempre (Microsoft Learn,
# "Flex Consumption how-to"). O build remoto instala o requirements.txt do pacote.
az functionapp deployment source config-zip --resource-group "$rg" --name "$funcao" \
  --src "$pacote" --build-remote true --output none

ler_chave() {
  # keys set não devolve o valor (sai redigido); o valor só vem do keys list.
  az functionapp keys list --resource-group "$rg" --name "$funcao" \
    --query "functionKeys.$chave" --output tsv
}

echo "== chave de host '$chave'"
valor="$(ler_chave)"
if [[ -z "$valor" ]]; then
  # Sem --key-value: a Azure gera o valor. A chave vale para as duas rotas (validar e reconciliar).
  az functionapp keys set --resource-group "$rg" --name "$funcao" \
    --key-type functionKeys --key-name "$chave" --output none
  valor="$(ler_chave)"
  echo "criada"
else
  echo "já existe"
fi
[[ -n "$valor" ]] || { echo "a chave '$chave' não apareceu no keys list" >&2; exit 1; }
# O Azure Pipelines mascara o valor em qualquer log depois desta linha.
echo "##vso[task.setsecret]$valor"

echo "== segredo $segredo em $cofre"
atual="$(az keyvault secret show --vault-name "$cofre" --name "$segredo" \
  --query value --output tsv 2>/dev/null || true)"
if [[ "$atual" == "$valor" ]]; then
  echo "igual; nenhuma versão nova"
  exit 0
fi
arquivo="$(mktemp)"
trap 'rm -f "$arquivo"' EXIT
chmod 600 "$arquivo"
printf '%s' "$valor" >"$arquivo"
# --file em vez de --value: o valor não fica na linha de comando (ps, histórico, log do agente).
# No primeiro apply, o papel Key Vault Secrets Officer do pipeline (rbac.tf) acabou de nascer e
# leva alguns minutos para valer: tenta de novo antes de desistir.
for tentativa in 1 2 3 4 5; do
  if az keyvault secret set --vault-name "$cofre" --name "$segredo" --file "$arquivo" \
      --encoding utf-8 --output none; then
    echo "gravado"
    exit 0
  fi
  echo "tentativa $tentativa falhou; nova tentativa em ${ESPERA_RBAC_S:-60} s" >&2
  sleep "${ESPERA_RBAC_S:-60}"
done
exit 1
