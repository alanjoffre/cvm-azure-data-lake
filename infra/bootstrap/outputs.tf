output "backend_config" {
  description = "Valores do -backend-config do terraform init dos ambientes (variáveis do pipeline)."
  value = {
    resource_group_name  = azurerm_resource_group.estado.name
    storage_account_name = azurerm_storage_account.estado.name
    container_name       = azurerm_storage_container.estado.name
  }
}

output "resource_groups" {
  description = "Resource group de cada ambiente."
  value       = { for k, rg in azurerm_resource_group.ambiente : k => rg.name }
}

output "condicao_rbac_admin" {
  description = "A condição aplicada ao RBAC Administrator do pipeline, para conferência."
  value       = local.condicao
}
