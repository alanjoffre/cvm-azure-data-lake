terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.50"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Sem backend: o bootstrap roda uma vez, por uma pessoa com Owner (ou User Access
  # Administrator + Contributor) na assinatura, e o estado fica local, fora do Git
  # (runbook, passo 1). É o que cria o lugar do estado remoto dos ambientes.
}

provider "azurerm" {
  storage_use_azuread = true

  features {
    storage {
      data_plane_available = false
    }
  }
}
