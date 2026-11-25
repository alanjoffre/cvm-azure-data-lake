# Permissões mínimas (arquitetura, seção 7). Cada atribuição diz quem, o quê e onde, no escopo
# mais estreito que funciona.
#
# O papel é referenciado pelo GUID que vem de papeis_atribuiveis.json, a mesma lista que monta a
# condição do RBAC Administrator no bootstrap. Um papel fora da lista quebra o plano aqui
# (chave inexistente no mapa), antes de a Azure recusar a atribuição no apply.

locals {
  atribuicoes = {
    # ADF: lê e grava bronze, staging e prata; só no lake.
    adf_lake = {
      papel     = "Storage Blob Data Contributor"
      escopo    = azurerm_storage_account.lake.id
      principal = azurerm_data_factory.adf.identity[0].principal_id
    }
    # ADF: lê a chave da Function; só neste cofre.
    adf_kv = {
      papel     = "Key Vault Secrets User"
      escopo    = azurerm_key_vault.kv.id
      principal = azurerm_data_factory.adf.identity[0].principal_id
    }
    # Function: lê a bronze e a prata, grava o ouro e o controle; só no lake.
    funcao_lake = {
      papel     = "Storage Blob Data Contributor"
      escopo    = azurerm_storage_account.lake.id
      principal = azurerm_function_app_flex_consumption.funcao.identity[0].principal_id
    }
    # Function: o host precisa de Blob Data Owner na própria conta (locks e pacote; Microsoft
    # Learn, "minimum storage account permissions"). Owner na conta do host, nunca no lake.
    funcao_host_blob = {
      papel     = "Storage Blob Data Owner"
      escopo    = azurerm_storage_account.host.id
      principal = azurerm_function_app_flex_consumption.funcao.identity[0].principal_id
    }
    # Function: eventos de diagnóstico do host, que a mesma página manda acrescentar.
    funcao_host_tabela = {
      papel     = "Storage Table Data Contributor"
      escopo    = azurerm_storage_account.host.id
      principal = azurerm_function_app_flex_consumption.funcao.identity[0].principal_id
    }
    # Identidade do pipeline (quem roda este Terraform): grava a chave da Function no cofre na
    # etapa de deploy, para o segredo não passar pelo estado (keyvault.tf).
    deploy_kv = {
      papel     = "Key Vault Secrets Officer"
      escopo    = azurerm_key_vault.kv.id
      principal = data.azurerm_client_config.atual.object_id
    }
  }
}

resource "azurerm_role_assignment" "papel" {
  for_each = local.atribuicoes

  scope              = each.value.escopo
  role_definition_id = "${data.azurerm_subscription.atual.id}/providers/Microsoft.Authorization/roleDefinitions/${var.papeis[each.value.papel]}"
  principal_id       = each.value.principal

  # Todas são identidades de serviço (as gerenciadas e a da federação do pipeline). A condição
  # do bootstrap só deixa atribuir papéis a principais do tipo ServicePrincipal, e declarar o tipo evita a espera pela
  # replicação do Entra ID logo depois de a identidade nascer.
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}
