# Runbook: implantar na Azure

Passo a passo para implantar o desenho numa assinatura da Azure, pelo portal, pela CLI e pelo
Azure DevOps. Os passos 1 a 5 são feitos uma vez, à mão; a partir do 6, quem implanta é o
pipeline ([`azure-pipelines.yml`](../azure-pipelines.yml)).

| Passo | O quê | Onde |
|---|---|---|
| 0 | Travas de custo | portal |
| 1 | Identidade do pipeline e bootstrap | máquina de quem tem `Owner` |
| 2 | Organização, projeto e pipeline no Azure DevOps | Azure DevOps |
| 3 | Agente próprio Linux, pool `cvm-lake` | máquina Linux |
| 4 | Service connection `sc-cvm-lake` por federação de identidade | Azure DevOps e Entra ID |
| 5 | Grupo de variáveis e environments com aprovação | Azure DevOps |
| 6 | Primeira execução: plano e apply em dev | pipeline |
| 7 | Deploy em dev: Function, depois o ADF | pipeline |
| 8 | Carga inicial (12 meses) e conferência | CLI |
| 9 | Reexecução e republicação na Azure | CLI |
| 10 | Conferir o que só a Azure confirma | lista em `testes.md` |
| 11 | Promoção para prod | pipeline, com aprovação |
| 12 | Destruir tudo e conferir o custo | máquina de quem tem `Owner` |

## 0. Antes de começar: travas de custo

- Assinatura com limite de gastos (*spending limit*) **ligado**, onde o tipo de assinatura o
  oferece (conta gratuita, crédito do Visual Studio). Pagamento por uso não tem esse limite: aí a
  trava é o orçamento abaixo e a data do passo 12.
- Orçamento (*budget*) com aviso por e-mail. O orçamento só avisa, e com atraso de horas, então
  não substitui o limite de gastos. O Terraform cria um por ambiente (`orcamento_mensal` em
  `infra/ambientes/*.tfvars`).
- Data marcada para o passo 12 (destroy).
- Não criar Data Flow, VNet gerenciada, endpoint privado nem Log Analytics.
- O agente do pipeline roda numa máquina Linux própria (passo 3), não numa VM da Azure.

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

# Provedores de recurso: registrar exige permissão na assinatura, que o pipeline não tem; o
# provider do ambiente tem resource_provider_registrations = "none".
for rp in Microsoft.DataFactory Microsoft.Web Microsoft.Storage Microsoft.KeyVault \
          Microsoft.Insights Microsoft.Consumption; do
  az provider register --namespace "$rp" --wait
done

export ARM_SUBSCRIPTION_ID="<ID da assinatura>"
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply
terraform -chdir=infra/bootstrap output backend_config   # vai para o grupo de variáveis (passo 5)
```

O estado do bootstrap fica local, em `infra/bootstrap/terraform.tfstate`, fora do Git
(`.gitignore`). Ele não guarda senha de aplicação, mas o provider grava atributos sensíveis da
conta: guardar como confidencial. A cópia também permite o destroy do passo 12. Guardar também o
`APP_ID`: o passo 4 o usa.

## 2. Organização, projeto e pipeline no Azure DevOps

1. Criar a organização e um projeto **privado**. O artefato do plano carrega os valores das
   variáveis, inclusive os e-mails do alerta, e o retrato do estado, com atributos sensíveis: é
   tratado como segredo. Em *Project settings* > *Pipelines* > *Settings*, baixar a retenção dos
   artefatos para poucos dias.
2. *Organization settings* > *Pipelines* > *Settings*: ligar *Limit building pull requests from
   forked GitHub repositories*.
3. *Pipelines* > *New pipeline* > *GitHub* > o repositório > *Existing Azure Pipelines YAML file*
   > `/azure-pipelines.yml`. A conexão com o GitHub é pelo GitHub App do Azure Pipelines, com
   acesso só a este repositório.
4. Salvar sem rodar (*Save*, não *Run*): o pool, a conexão e o grupo ainda não existem.
5. No pipeline, *Edit* > *Triggers* > *Pull request validation*: confirmar que *Build pull
   requests from forks of this repository* está desligado. O YAML já tem `pr: none`, e esta
   opção da interface é a segunda trava: nenhum código de pull request roda no agente próprio
   nem com a service connection. Os pull requests são validados pelo
   [GitHub Actions](../.github/workflows/ci.yml), sem credencial.

## 3. Agente próprio (Linux), pool `cvm-lake`

1. *Organization settings* > *Agent pools* > *Add pool* > *Self-hosted*, nome `cvm-lake`, com
   *Grant access permission to all pipelines* desligado.
2. Na máquina Linux do agente, instalar:

   | Ferramenta | Versão | Para quê |
   |---|---|---|
   | git | atual | checkout |
   | Python | 3.12 | scripts de deploy (`python3`) |
   | uv | atual | dependências do `uv.lock`, testes, Checkov |
   | Node | 22 (LTS) | pacote do ADF (`@microsoft/azure-data-factory-utilities`) |
   | Terraform | 1.11.2 ou mais nova | o backend renova o token OIDC do Azure DevOps |
   | tflint | atual | `infra/validar.sh` |
   | Azure CLI | atual | tasks `AzureCLI@2` e `funcao.sh` (o `config-zip` com build remoto no Flex Consumption) |
   | PowerShell | 7 (`pwsh`) | arm-ttk e o pré-script e o pós-script do ADF |
   | módulo Az do PowerShell | atual | `AzurePowerShell@5` (`Install-Module Az -Scope CurrentUser`) |

3. Baixar o agente em *Agent pools* > `cvm-lake` > *New agent* > *Linux*, e configurar com um
   *personal access token* de escopo *Agent Pools (Read & manage)*, que só serve para o
   registro:

   ```bash
   ./config.sh --url https://dev.azure.com/<organização> --auth pat --pool cvm-lake
   sudo ./svc.sh install && sudo ./svc.sh start
   ```

4. Apagar o token depois do registro. O agente roda como serviço, com um usuário sem `sudo`.

## 4. Service connection por federação de identidade

A conexão `sc-cvm-lake` autentica o pipeline como o aplicativo do passo 1, sem segredo de
cliente: o Azure DevOps emite um token, e o Entra ID o troca pelo token da Azure.

1. *Project settings* > *Service connections* > *New service connection* > *Azure Resource
   Manager* > *App registration or managed identity (manual)* > *Workload identity federation*.
   Nome: `sc-cvm-lake`. A tela mostra o *Issuer* e o *Subject identifier*.
2. Criar a credencial federada no aplicativo do passo 1, com os dois valores da tela:

   ```bash
   cat > credencial.json <<'JSON'
   {
     "name": "ado-sc-cvm-lake",
     "issuer": "<Issuer da tela>",
     "subject": "<Subject identifier da tela>",
     "audiences": ["api://AzureADTokenExchange"]
   }
   JSON
   az ad app federated-credential create --id "$APP_ID" --parameters credencial.json
   ```

3. Completar a conexão com a assinatura, o tenant e o *Client ID* (`APP_ID`). Escopo: a
   assinatura. A identidade só tem os papéis do bootstrap, nos dois resource groups e no
   container do estado.
4. Deixar *Grant access permission to all pipelines* desligado.
5. Na conexão, *Approvals and checks* > *Branch control*: só `refs/heads/main`. O estágio do
   plano de prod roda antes da aprovação, com esta conexão; o check garante que só código da
   main a usa. A separação completa é uma segunda conexão, com `Reader` e leitura do estado, só
   para os planos (D10).

## 5. Variáveis do pipeline e ambientes `dev` e `prod` com aprovação

**Grupo de variáveis.** *Pipelines* > *Library* > *+ Variable group*, nome `cvm-lake`:

| Variável | Valor | Secreta |
|---|---|---|
| `TF_ESTADO_RG` | `resource_group_name` de `backend_config` (passo 1) | não |
| `TF_ESTADO_CONTA` | `storage_account_name` de `backend_config` | não |
| `TF_ESTADO_CONTAINER` | `container_name` de `backend_config` | não |
| `EMAILS_ALERTA` | a lista de e-mails do alerta, em HCL: `["alerta@example.com"]` | **sim** |

A variável secreta só chega ao script mapeada no `env` da task (o `TF_VAR_emails_alerta` do
[`deploy.yml`](../ci/azure/deploy.yml)). Em *Pipeline permissions* do grupo, liberar só este
pipeline.

O `terraform.sh` lê o ID da service connection em `AZURESUBSCRIPTION_SERVICE_CONNECTION_ID`,
que a task `AzureCLI@2` define, mas a página da task não documenta. Se a primeira execução
parar dizendo que ela falta, acrescentar ao grupo `ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID` com o
ID da conexão (o GUID em *Service connections* > `sc-cvm-lake`, que não é segredo).

**Environments.** *Pipelines* > *Environments* > *New environment* > *None*:

| Environment | *Approvals and checks* |
|---|---|
| `cvm-lake-dev` | *Branch control*: só `refs/heads/main` |
| `cvm-lake-prod` | *Branch control*: só `refs/heads/main`; *Approvals*: quem aprova, com *Approvers can't approve their own runs* ligado e prazo de 1 dia |

As aprovações e os checks existem só na interface, não no YAML. O Azure Pipelines avalia os
checks de um environment antes de o estágio que o usa começar: por isso o plano roda num estágio
separado, e quem aprova lê o plano antes (passo 11).

**Permissões.** Na primeira execução, o Azure DevOps pede permissão para o pipeline usar o pool,
a conexão, o grupo e os environments (*This pipeline needs permission to access a resource*).
Liberar só para este pipeline.

## 6. Primeira execução: plano e apply em dev

Um push na main (ou *Run pipeline* na main) dispara:

1. **CI**: `uv sync`, ruff, mypy, pytest, `infra/validar.sh`, `ci/adf/validar.sh` (validate e
   export do ARM), `ci/adf/armttk.sh` e o pacote da Function. Publica os artefatos `arm` e
   `funcao`.
2. **Plano dev**: [`ci/deploy/terraform.sh`](../ci/deploy/terraform.sh) `plano dev`. Publica o
   artefato `plano_dev`.
3. **Deploy dev**: no environment `cvm-lake-dev`, `terraform.sh aplicar dev` aplica o plano
   publicado, não um plano novo. As saídas (`lake_dfs_endpoint`, `funcao_host`,
   `key_vault_uri` etc.) viram os parâmetros do ARM e as variáveis do job.

O `terraform.sh` roda dentro da task `AzureCLI@2` com `addSpnToEnvironment`, e monta o que o
provider e o backend leem: `ARM_USE_OIDC=true`, `ARM_CLIENT_ID`, `ARM_TENANT_ID`,
`ARM_SUBSCRIPTION_ID` e o ID da service connection, com `SYSTEM_ACCESSTOKEN` e
`SYSTEM_OIDCREQUESTURI` mapeados no `env`. Assim o token se renova durante um apply longo. Sem o
token mapeado, o script para com a mensagem do que falta.

Para depurar fora do pipeline, entrar com `az login` como alguém que tem acesso ao estado (passo
12). Fora do pipeline, só `plan`: o papel do cofre vai para a identidade que roda o Terraform, e o
plano mostra a troca dele para o seu usuário, que não deve ser aplicada.

```bash
terraform -chdir=infra init \
  -backend-config="resource_group_name=<TF_ESTADO_RG>" \
  -backend-config="storage_account_name=<TF_ESTADO_CONTA>" \
  -backend-config="container_name=<TF_ESTADO_CONTAINER>" \
  -backend-config="key=dev.tfstate"
TF_VAR_emails_alerta='["alerta@example.com"]' \
  terraform -chdir=infra plan -var-file=ambientes/dev.tfvars
```

Prod é igual, com `key=prod.tfstate` e `ambientes/prod.tfvars`.

## 7. Deploy em dev: primeiro a Function, depois o ADF

Ainda no estágio **Deploy dev**, depois do apply:

| Ordem | Passo | Por quê |
|---|---|---|
| 1 | [`ci/deploy/funcao.sh`](../ci/deploy/funcao.sh): `az functionapp deployment source config-zip --build-remote true` com o `funcao.zip` | o Flex Consumption instala o `requirements.txt` no build remoto; o zip tem `function_app.py`, `host.json` e o pacote na raiz |
| 2 | o mesmo script cria a chave de host `adf` (a Azure gera o valor) e a grava no cofre como `funcao-chave-adf` | o linked service da Function lê a chave no cofre; o valor não passa pelo estado do Terraform nem pelo log (`##vso[task.setsecret]`) |
| 3 | pré-script do ADF (`PrePostDeploymentScript.ps1`, gerado com o ARM, `-predeployment $true`), com `-ExplicitStopTriggerList tr_diario,tr_semanal` e `-ArmTemplateParameters` deste ambiente | interrompe os dois gatilhos; sozinho, o script só interrompe os que mudaram |
| 4 | `az deployment group create --mode Incremental` com o ARM e os parâmetros das saídas | o modo `Complete` apagaria do resource group o que não está no template, inclusive o lake e a Function |
| 5 | pós-script (`-predeployment $false -deleteDeployment $true`) | apaga do ADF o que saiu do JSON e religa os gatilhos com `runtimeState` `Started` |

Se o ARM falhar, os gatilhos ficam parados: nenhuma carga nova começa sobre um ADF pela metade.
Parar um gatilho não cancela uma execução em andamento: antes de um deploy que muda o pipeline,
conferir em *Monitor* que nenhuma carga está rodando. Corrigir e rodar de novo; o pré-script é
idempotente.

**Na primeira vez**, o papel `Key Vault Secrets Officer` do pipeline nasce no mesmo apply, e a
Azure leva alguns minutos para a atribuição valer. O `funcao.sh` tenta gravar o segredo 5 vezes,
com 60 s entre elas. Se ainda assim falhar, rodar de novo o estágio (*Rerun failed jobs*). Numa
segunda execução com a mesma chave, o script vê que o segredo é igual e não grava versão nova.

Conferir no portal: a Function com as duas funções (`validar` e `reconciliar`), o ADF com os dois
pipelines e os dois gatilhos iniciados, e o cofre com o segredo.

## 8. Carga inicial (12 meses) e conferência da reconciliação

O gatilho diário carrega só o mês corrente e o anterior. A carga inicial dispara o pipeline pai
com a janela de 12 meses (`de = 0`, `ate = 11`):

```bash
az extension add --name datafactory
RG=rg-cvm-lake-dev
ADF=<adf_nome, saída do Terraform>
RUN=$(az datafactory pipeline create-run -g $RG --factory-name $ADF --name pl_inf_diario \
  --parameters '{"de": 0, "ate": 11}' --query runId -o tsv)
az datafactory pipeline-run show -g $RG --factory-name $ADF --run-id $RUN --query status
```

Esperar `Succeeded`. Uma divergência faz o pipeline falhar com `CVM_VALIDACAO` ou
`CVM_RECONCILIACAO`, e o alerta manda e-mail.

Para ler o controle, quem confere precisa de `Storage Blob Data Reader` na conta do lake (o
`Owner` não dá acesso aos dados). Atribuir só durante a conferência:

```bash
LAKE=<lake_conta, saída do Terraform>
az role assignment create --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Storage Blob Data Reader" \
  --scope "$(az storage account show -n $LAKE -g $RG --query id -o tsv)"

az storage blob download --auth-mode login --account-name $LAKE -c controle \
  -n cvm/inf_diario/competencia=202609/manifesto.json -f manifesto_202609.json
```

Para cada um dos 12 meses, o `manifesto.json` precisa ter `carga_vigente` igual ao `RUN` (o
`RunId` do pipeline pai) e `origem.confere_com_bronze` verdadeiro. Ao lado dele, o
`carga=<RUN>.json` do mês tem a validação e a reconciliação completas: bronze e prata com as
mesmas `linhas` e o mesmo `sha256` do manifesto, e o ouro conferido contra a prata.

## 9. Reexecução e republicação na Azure

Na máquina, a republicação é simulada (D7). Na Azure, ela é a da própria CVM, que regrava o mês
corrente e o anterior todo dia.

1. **Reexecução.** Rodar de novo o comando do passo 8. Os 12 manifestos mudam `carga_vigente`,
   e `linhas` e `sha256` ficam iguais: recarregar não duplica nem perde linha.
2. **Republicação.** Guardar os 12 manifestos e, no dia seguinte, depois do gatilho diário,
   baixar de novo. Só podem mudar os meses que o gatilho recarregou (o corrente e o anterior); os
   outros ficam com o mesmo `sha256`. Na bronze, o mês recarregado tem as duas cargas
   (`competencia=AAAAMM/carga=<RUN_ID>/`): quem guarda a versão anterior é a pasta da carga
   (D5), apagada em 90 dias. A conta do lake não tem versionamento de blob.
3. Se a CVM tiver regravado o mês, `linhas` ou `sha256` mudam e o `origem.etag` também. Se não
   tiver, os dois ficam iguais e a carga nova só troca `carga_vigente`.

## 10. Conferir o que só a Azure confirma

A lista de [`testes.md`](testes.md#só-na-azure), item por item, com o registro de cada
conferência (data, execução, o que se viu).

## 11. Promoção para prod

Depois do **Deploy dev** verde, a mesma execução segue:

1. **Plano prod** roda sozinho e publica `plano_prod`. Quem aprova lê o log da task
   `terraform plan` desse estágio: o que será criado, alterado ou destruído.
2. **Deploy prod** fica esperando a aprovação no environment `cvm-lake-prod`. Aprovar só se o
   plano for o esperado. Rejeitar ou deixar vencer o prazo encerra a execução sem tocar em prod.
3. Aprovado, o estágio aplica o plano publicado e faz a mesma sequência do passo 7 com os
   mesmos artefatos `arm` e `funcao` de dev: prod recebe o que passou em dev, sem rebuild.
4. Fazer a carga inicial do passo 8 em prod, com `RG=rg-cvm-lake-prod`.

Se o estado mudar entre o plano e a aprovação (outra execução aplicou antes), o Terraform recusa
o plano velho. Rodar o pipeline de novo.

## 12. Destruir tudo e conferir que não sobrou recurso

O pipeline não destrói nada. O destroy é feito na máquina de quem tem `Owner`, que precisa de
`Storage Blob Data Contributor` na conta do estado (o backend lê o estado sem chave):

```bash
az role assignment create --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Storage Blob Data Contributor" \
  --scope "$(az storage account show -n <TF_ESTADO_CONTA> -g <TF_ESTADO_RG> --query id -o tsv)"

export TF_VAR_emails_alerta='["alerta@example.com"]'
# init com key=prod.tfstate (passo 6)
terraform -chdir=infra plan -destroy -var-file=ambientes/prod.tfvars -out=destroy.tfplan
terraform -chdir=infra apply destroy.tfplan
# repetir para dev (init -reconfigure com key=dev.tfstate)
```

Depois, o bootstrap. A conta e o container do estado têm `prevent_destroy`, para nenhum plano
apagar o estado por engano: tirar as duas linhas `prevent_destroy = true` de
`infra/bootstrap/main.tf` e rodar `terraform -chdir=infra/bootstrap destroy`.

Conferir:
- `az resource list --query "[?tags.projeto=='cvm-lake']"` vazio;
- o Key Vault apagado fica 7 dias em soft delete, sem custo;
- no Azure DevOps, apagar a service connection, o pool e o agente e, no Entra ID, o aplicativo
  `sp-cvm-lake-pipeline`;
- em *Cost Management* > *Cost analysis*, no dia seguinte, o custo do período por serviço. O
  custo aparece com atraso de horas.
