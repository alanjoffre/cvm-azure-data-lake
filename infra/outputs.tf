output "lake_conta" {
  description = "Conta do ADLS Gen2."
  value       = module.ambiente.lake_conta
}

output "lake_dfs_endpoint" {
  description = "Endpoint dfs do lake."
  value       = module.ambiente.lake_dfs_endpoint
}

output "adf_nome" {
  description = "Data Factory do ambiente."
  value       = module.ambiente.adf_nome
}

output "funcao_nome" {
  description = "Function do ambiente."
  value       = module.ambiente.funcao_nome
}

output "funcao_host" {
  description = "Endereço da Function."
  value       = module.ambiente.funcao_host
}

output "key_vault_nome" {
  description = "Cofre do ambiente."
  value       = module.ambiente.key_vault_nome
}

output "key_vault_uri" {
  description = "URI do cofre."
  value       = module.ambiente.key_vault_uri
}

output "papeis_atribuidos" {
  description = "Papéis que o ambiente atribui."
  value       = module.ambiente.papeis_atribuidos
}

output "resource_group_name" {
  description = "Resource group do ambiente (alvo do deploy do ARM e da Function)."
  value       = var.resource_group_name
}
