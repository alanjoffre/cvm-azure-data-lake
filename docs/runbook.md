# Runbook: implantar na Azure

Passo a passo para implantar o desenho numa assinatura da Azure, pelo portal e pela CLI.

> Em construção. As seções abaixo são o índice; o conteúdo entra com o Terraform, o ADF e o
> pipeline.

## 0. Antes de começar: travas de custo

- Conta gratuita da Azure com o limite de gastos (*spending limit*) **ligado**. Não converter
  para pagamento por uso durante o teste.
- Orçamento (*budget*) com aviso por e-mail. O orçamento só avisa, e com atraso de horas, então
  não substitui o limite de gastos.
- Data marcada para `terraform destroy`, antes do fim do crédito.
- Não criar Data Flow, VNet gerenciada, endpoint privado nem Log Analytics.

## 1. Estado remoto do Terraform

Quem cria o resource group e a conta de armazenamento do estado, e com que permissão de dados
o Terraform lê e grava o estado sem chave compartilhada.

## 2. Organização e projeto no Azure DevOps

Ligar ao repositório do GitHub; desligar o build de PR de fork (*Limit building pull requests
from forked GitHub repositories*).

## 3. Agente próprio (Linux)

Instalação do agente, `pwsh` e o módulo Az (para o script de parar e religar os gatilhos), Node
(para o pacote do ADF) e Terraform.

## 4. Service connection por federação de identidade

## 5. Variáveis do pipeline e ambientes `dev` e `prod` com aprovação

## 6. Primeira execução: `terraform apply` em dev

## 7. Deploy em dev: primeiro a Function, depois o ADF

O linked service do ADF aponta para a Function, então ela sobe antes.

## 8. Carga inicial (12 meses) e conferência da reconciliação

## 9. Republicação simulada na Azure

## 10. Conferir o que só a Azure confirma

A lista de [`testes.md`](testes.md#só-na-azure), item por item.

## 11. Promoção para prod

## 12. Destruir tudo e conferir que não sobrou recurso
