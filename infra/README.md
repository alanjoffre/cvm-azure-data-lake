# Infraestrutura (Terraform)

Dois ambientes iguais, dev e prod, pelo mesmo módulo (D8). Não aplicado na Azure: validado na
máquina com `infra/validar.sh`. O roteiro de implantação está no [runbook](../docs/runbook.md).

```
infra/
├── bootstrap/               uma vez, por quem tem Owner: estado remoto, resource groups e
│                            as permissões do pipeline (condição do RBAC Administrator)
├── modules/ambiente/        um ambiente inteiro
│   ├── lake.tf              ADLS Gen2, containers das camadas e ciclo de vida
│   ├── adf.tf               Data Factory com identidade, sem integração Git (D9)
│   ├── funcao.tf            Function no Flex Consumption e a conta do host
│   ├── keyvault.tf          cofre por RBAC; o segredo é gravado no deploy (D11)
│   ├── rbac.tf              as 6 atribuições de papel
│   ├── monitoramento.tf     alerta de falha do pipeline e orçamento
│   └── tests/               terraform test com provider simulado
├── ambientes/dev.tfvars     o que muda entre os ambientes
├── ambientes/prod.tfvars
├── papeis_atribuiveis.json  os papéis que o pipeline pode atribuir: o bootstrap monta a
│                            condição com eles, e o módulo só atribui o que está aqui
├── tests/                   a raiz com o .tfvars de cada ambiente
└── validar.sh               fmt, validate, tflint, Checkov e os testes
```

## O que fica fora do repositório

| Valor | Por onde chega |
|---|---|
| Assinatura e tenant | `ARM_SUBSCRIPTION_ID` e `ARM_TENANT_ID` (e `ARM_USE_OIDC` no pipeline) |
| E-mails do alerta e do orçamento | `TF_VAR_emails_alerta`, variável secreta do pipeline |
| Conta do estado remoto | `-backend-config` no `terraform init` |
| Object ID da identidade do pipeline | `TF_VAR_pipeline_principal_id`, só no bootstrap |
| Chave da Function | criada e gravada no Key Vault pela etapa de deploy, nunca no estado |

## Permissões

| Quem | Papel | Onde |
|---|---|---|
| ADF | Storage Blob Data Contributor | conta do lake |
| ADF | Key Vault Secrets User | cofre do ambiente |
| Function | Storage Blob Data Contributor | conta do lake |
| Function | Storage Blob Data Owner e Storage Table Data Contributor | conta do host (o mínimo que a Microsoft documenta para o host) |
| Pipeline | Key Vault Secrets Officer | cofre do ambiente (para gravar a chave da Function) |
| Pipeline | Contributor e Role Based Access Control Administrator com condição | resource group do ambiente (bootstrap) |
| Pipeline | Storage Blob Data Contributor | container do estado (bootstrap) |

A condição só deixa o pipeline criar e remover atribuições dos papéis de
`papeis_atribuiveis.json`, e só para entidades de serviço (*service principals*). Com ela, o pipeline não pode se
atribuir `Owner` e não dá
papel a pessoas nem a grupos.

## Validar

Precisa de Terraform 1.11.2+, [tflint](https://github.com/terraform-linters/tflint) e
[uv](https://docs.astral.sh/uv/) (para o Checkov).

```bash
infra/validar.sh
```

Nenhuma etapa usa conta da Azure. O `terraform test` simula o provider (`mock_provider`) e
confere o plano. O que só a Azure confirma está em
[`docs/testes.md`](../docs/testes.md#só-na-azure).
