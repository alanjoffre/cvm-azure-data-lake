# Runbook: implantar na Azure

Passo a passo para implantar o desenho numa assinatura da Azure, pelo portal e pela CLI.

> Em construção. Os passos 1, 6 e 12 (Terraform) estão escritos; os outros entram com o ADF e o
> pipeline.

## 0. Antes de começar: travas de custo

- Conta gratuita da Azure com o limite de gastos (*spending limit*) **ligado**. Não converter
  para pagamento por uso durante o teste.
- Orçamento (*budget*) com aviso por e-mail. O orçamento só avisa, e com atraso de horas, então
  não substitui o limite de gastos.
- Data marcada para `terraform destroy`, antes do fim do crédito.
- Não criar Data Flow, VNet gerenciada, endpoint privado nem Log Analytics.

## 1. Identidade do pipeline e bootstrap

Feito uma vez, por quem tem `Owner` na assinatura, na própria máquina (Azure CLI e Terraform).
O [bootstrap](../infra/bootstrap/) cria o que a identidade do pipeline não pode criar sozinha:
- a conta do estado remoto (sem chave compartilhada, com versionamento de blob) e o container
  `tfstate`;
- os resource groups `rg-cvm-lake-dev` e `rg-cvm-lake-prod`;
- as permissões do pipeline: `Storage Blob Data Contributor` só no container do estado;
  `Contributor` e `Role Based Access Control Administrator` (com a condição) só em cada resource
  group.

```bash
az login
az account set --subscription "<ID da assinatura>"

# Identidade do pipeline: um registro de aplicativo, sem senha. A credencial federada entra no
# passo 4, quando a service connection do Azure DevOps existir.
APP_ID=$(az ad app create --display-name sp-cvm-lake-pipeline --query appId -o tsv)
export TF_VAR_pipeline_principal_id=$(az ad sp create --id "$APP_ID" --query id -o tsv)

export ARM_SUBSCRIPTION_ID="<ID da assinatura>"
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply
terraform -chdir=infra/bootstrap output backend_config   # vai para as variáveis do pipeline
```

O estado do bootstrap fica local, em `infra/bootstrap/terraform.tfstate`, fora do Git
(`.gitignore`). Ele não tem segredo, mas guardar uma cópia permite o destroy do passo 12.

## 2. Organização e projeto no Azure DevOps

Ligar ao repositório do GitHub; desligar o build de PR de fork (*Limit building pull requests
from forked GitHub repositories*).

## 3. Agente próprio (Linux)

Instalação do agente, `pwsh` e o módulo Az (para o script de parar e religar os gatilhos), Node
(para o pacote do ADF) e Terraform.

## 4. Service connection por federação de identidade

## 5. Variáveis do pipeline e ambientes `dev` e `prod` com aprovação

## 6. Primeira execução: `terraform apply` em dev

O pipeline roda, com a identidade da service connection (`ARM_USE_OIDC=true`,
`ARM_CLIENT_ID`, `ARM_TENANT_ID`, `ARM_SUBSCRIPTION_ID`) e os e-mails de alerta numa variável
secreta (`TF_VAR_emails_alerta='["..."]'`):

```bash
terraform -chdir=infra init \
  -backend-config="resource_group_name=rg-cvm-lake-tfstate" \
  -backend-config="storage_account_name=<saída do bootstrap>" \
  -backend-config="container_name=tfstate" \
  -backend-config="key=dev.tfstate"
terraform -chdir=infra plan -var-file=ambientes/dev.tfvars -out=dev.tfplan
terraform -chdir=infra apply dev.tfplan
```

Prod é igual, com `key=prod.tfstate` e `ambientes/prod.tfvars`. As saídas (`lake_conta`,
`funcao_host`, `key_vault_uri` etc.) viram os parâmetros do ARM do ADF no passo 7.

## 7. Deploy em dev: primeiro a Function, depois o ADF

O linked service do ADF aponta para a Function, então ela sobe antes.

## 8. Carga inicial (12 meses) e conferência da reconciliação

## 9. Republicação simulada na Azure

## 10. Conferir o que só a Azure confirma

A lista de [`testes.md`](testes.md#só-na-azure), item por item.

## 11. Promoção para prod

## 12. Destruir tudo e conferir que não sobrou recurso

```bash
terraform -chdir=infra plan -destroy -var-file=ambientes/prod.tfvars -out=destroy.tfplan
terraform -chdir=infra apply destroy.tfplan
# repetir para dev (init com key=dev.tfstate)
```

Depois, o bootstrap. A conta e o container do estado têm `prevent_destroy`, para nenhum plano
apagar o estado por engano: tirar as duas linhas `prevent_destroy = true` de
`infra/bootstrap/main.tf` e rodar `terraform -chdir=infra/bootstrap destroy`. Conferir com
`az resource list --query "[?tags.projeto=='cvm-lake']"` que não sobrou nada. O Key Vault apagado
fica 7 dias em soft delete, sem custo.
