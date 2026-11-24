# A assinatura e o tenant chegam pelo ambiente (ARM_SUBSCRIPTION_ID, ARM_TENANT_ID e, no
# pipeline, ARM_USE_OIDC com a federação de identidade). Nenhum ID no repositório.
provider "azurerm" {
  # As contas têm a chave compartilhada desligada: o provider fala com o armazenamento pelo
  # Entra ID.
  storage_use_azuread = true

  features {
    storage {
      # O recurso de conta de armazenamento lê propriedades do plano de dados (filas, site
      # estático) por padrão. Nenhuma das duas é usada aqui; desligado, a identidade do pipeline
      # não precisa de papel de dados nas contas que ela mesma cria.
      data_plane_available = false
    }

    key_vault {
      # Purgar um cofre exige permissão na assinatura, que o pipeline não tem. O cofre apagado
      # fica 7 dias em soft delete e some sozinho; o sufixo aleatório evita colisão.
      purge_soft_delete_on_destroy = false
    }

    resource_group {
      # O resource group é do bootstrap; este Terraform nunca o apaga.
      prevent_deletion_if_contains_resources = true
    }
  }
}
