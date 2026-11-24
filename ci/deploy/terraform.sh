#!/usr/bin/env bash
# Plano e aplicação do Terraform de um ambiente, em dois passos, para a aprovação ver o plano
# que será aplicado (azure-pipelines.yml: job do plano → aprovação no environment → apply).
#   ci/deploy/terraform.sh plano   <dev|prod> <arquivo.tfplan>
#   ci/deploy/terraform.sh aplicar <dev|prod> <arquivo.tfplan> <saidas.json>
#
# Ambiente esperado (variáveis do pipeline; nada disto fica no repositório):
#   TF_ESTADO_RG, TF_ESTADO_CONTA, TF_ESTADO_CONTAINER  saída backend_config do bootstrap
#   TF_VAR_emails_alerta                                 destinatários do orçamento (secreta)
#   SYSTEM_ACCESSTOKEN, SYSTEM_OIDCREQUESTURI            federação, sem segredo de cliente
# Dentro do AzureCLI@2 com addSpnToEnvironment, o client ID, o tenant e o ID da service
# connection vêm da própria task. Com o ID da service connection, o provider e o backend pedem
# um token novo ao Azure DevOps quando precisam (o idToken da task expira em minutos).
set -euo pipefail
acao="${1:-}" ambiente="${2:-}" plano="${3:-}"
case "$acao:$ambiente" in
  plano:dev|plano:prod) [[ $# -eq 3 ]] ;;
  aplicar:dev|aplicar:prod) [[ $# -eq 4 ]] ;;
  *) false ;;
esac || { sed -n 2,5p "$0" >&2; exit 2; }
raiz_repo="$(cd "$(dirname "$0")/../.." && pwd)"
plano="$(realpath -m "$plano")"

: "${TF_ESTADO_RG:?}" "${TF_ESTADO_CONTA:?}" "${TF_ESTADO_CONTAINER:?}"
export ARM_CLIENT_ID="${ARM_CLIENT_ID:-${servicePrincipalId:?rode dentro do AzureCLI@2 com addSpnToEnvironment}}"
export ARM_TENANT_ID="${ARM_TENANT_ID:-${tenantId:?}}"
export ARM_SUBSCRIPTION_ID="${ARM_SUBSCRIPTION_ID:-$(az account show --query id --output tsv)}"
export ARM_USE_OIDC=true
export ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID="${ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID:-${AZURESUBSCRIPTION_SERVICE_CONNECTION_ID:?}}"
: "${SYSTEM_ACCESSTOKEN:?mapeie \$(System.AccessToken) no env da task}" "${SYSTEM_OIDCREQUESTURI:?}"
export TF_IN_AUTOMATION=1 TF_INPUT=0

cd "$raiz_repo/infra"
# Uma chave de estado por ambiente no mesmo container do bootstrap.
terraform init -no-color -reconfigure \
  -backend-config="resource_group_name=$TF_ESTADO_RG" \
  -backend-config="storage_account_name=$TF_ESTADO_CONTA" \
  -backend-config="container_name=$TF_ESTADO_CONTAINER" \
  -backend-config="key=$ambiente.tfstate"

if [[ "$acao" == "plano" ]]; then
  : "${TF_VAR_emails_alerta:?}"
  mkdir -p "$(dirname "$plano")"
  terraform plan -no-color -var-file="ambientes/$ambiente.tfvars" -out="$plano"
  exit 0
fi

saidas="$(realpath -m "$4")"
# O plano salvo carrega as variáveis do plano: o apply não relê tfvars. Se o estado mudou
# desde o plano, o Terraform recusa o plano velho em vez de aplicar outra coisa.
terraform apply -no-color "$plano"
mkdir -p "$(dirname "$saidas")"
terraform output -json >"$saidas"
