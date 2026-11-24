terraform {
  # 1.11.2: o backend azurerm renova o token OIDC do Azure DevOps no meio do apply.
  required_version = ">= 1.11.2"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.50"
    }
  }

  # Estado remoto no container criado pelo bootstrap. Configuração parcial: conta, container e
  # chave chegam por -backend-config no init (runbook, passo 1), para nenhum nome de conta ou
  # assinatura ficar no repositório. use_azuread_auth: o estado também é lido sem chave.
  backend "azurerm" {
    use_azuread_auth = true
  }
}
