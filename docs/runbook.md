# Runbook: implantar na Azure

Passo a passo para implantar o desenho numa assinatura da Azure, pelo portal e pela CLI.
Nada aqui foi aplicado: é o roteiro de implantação (ver
[Limites declarados](../README.md#limites-declarados)).

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

## 2. Organização e projeto no Azure DevOps

## 3. Agente próprio (Linux)

## 4. Service connection por federação de identidade

## 5. Variáveis do pipeline e ambientes `dev` e `prod` com aprovação

## 6. Primeira execução: `terraform apply` em dev

## 7. Deploy do ADF e da Function em dev

## 8. Carga inicial (12 meses) e conferência da reconciliação

## 9. Republicação simulada na Azure

## 10. Promoção para prod

## 11. Destruir tudo e conferir que não sobrou recurso
