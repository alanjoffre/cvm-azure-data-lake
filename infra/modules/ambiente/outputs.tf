output "lake_conta" {
  description = "Conta do ADLS Gen2 (parâmetro do ARM do ADF e app setting da Function)."
  value       = azurerm_storage_account.lake.name
}

output "lake_dfs_endpoint" {
  description = "Endpoint dfs do lake (linked service do ADF)."
  value       = azurerm_storage_account.lake.primary_dfs_endpoint
}

output "adf_nome" {
  description = "Data Factory que recebe o ARM do build."
  value       = azurerm_data_factory.adf.name
}

output "funcao_nome" {
  description = "Function que recebe o pacote no deploy."
  value       = azurerm_function_app_flex_consumption.funcao.name
}

output "funcao_host" {
  description = "Endereço da Function (linked service do ADF)."
  value       = "https://${azurerm_function_app_flex_consumption.funcao.default_hostname}"
}

output "key_vault_nome" {
  description = "Cofre onde o deploy grava a chave da Function."
  value       = azurerm_key_vault.kv.name
}

output "key_vault_uri" {
  description = "URI do cofre (linked service do Key Vault no ADF)."
  value       = azurerm_key_vault.kv.vault_uri
}

output "papeis_atribuidos" {
  description = "Nome de cada papel que o módulo atribui. Os testes cobram que todos estão na condição do bootstrap."
  value       = { for k, a in local.atribuicoes : k => a.papel }
}
