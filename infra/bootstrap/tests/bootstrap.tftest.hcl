# Testes do bootstrap sem conta na Azure (provider simulado). O foco é a condição do RBAC
# Administrator: o pipeline só atribui os papéis da lista, e só a identidades de serviço.
#
#   cd infra/bootstrap && terraform init && terraform test

mock_provider "azurerm" {
  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-teste"
    }
  }
  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-tfstate/providers/Microsoft.Storage/storageAccounts/stteste"
    }
  }

  mock_resource "azurerm_storage_container" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-tfstate/providers/Microsoft.Storage/storageAccounts/stteste/blobServices/default/containers/tfstate"
    }
  }

  override_data {
    target = data.azurerm_subscription.atual
    values = {
      id              = "/subscriptions/00000000-0000-0000-0000-000000000000"
      subscription_id = "00000000-0000-0000-0000-000000000000"
    }
  }
}

variables {
  pipeline_principal_id = "55555555-5555-5555-5555-555555555555"
}

run "estado_remoto_sem_chave_e_versionado" {
  command = plan

  assert {
    condition     = !azurerm_storage_account.estado.shared_access_key_enabled && azurerm_storage_account.estado.default_to_oauth_authentication
    error_message = "O estado é lido e gravado por Entra ID, sem chave compartilhada."
  }
  assert {
    condition     = azurerm_storage_account.estado.blob_properties[0].versioning_enabled && !azurerm_storage_account.estado.is_hns_enabled
    error_message = "Versionamento de blob ligado (exige conta sem HNS)."
  }
  assert {
    condition     = azurerm_storage_container.estado.container_access_type == "private"
    error_message = "O container do estado é privado."
  }
}

run "um_resource_group_por_ambiente" {
  command = plan

  assert {
    condition     = toset([for rg in azurerm_resource_group.ambiente : rg.name]) == toset(["rg-cvm-lake-dev", "rg-cvm-lake-prod"])
    error_message = "Os nomes precisam bater com resource_group_name de ambientes/dev.tfvars e prod.tfvars."
  }
}

run "condicao_so_papeis_da_lista_e_so_servico" {
  command = plan

  assert {
    condition     = alltrue([for a in azurerm_role_assignment.pipeline_rbac_admin : a.condition_version == "2.0"])
    error_message = "Condição na versão 2.0."
  }
  assert {
    condition     = length(local.papeis) == 5 && local.papeis == jsondecode(file("../papeis_atribuiveis.json"))
    error_message = "A condição usa a mesma lista que o módulo do ambiente (papeis_atribuiveis.json)."
  }
  assert {
    condition     = alltrue([for g in values(local.papeis) : length(regexall(g, local.condicao)) == 2])
    error_message = "Cada papel da lista aparece nas duas metades da condição (criar e remover)."
  }
  assert {
    # Owner, User Access Administrator, RBAC Administrator e Contributor nunca podem ser
    # atribuídos pelo pipeline.
    condition = alltrue([for g in [
      "8e3af657-a8ff-443f-a75c-2fe8c4bcb635",
      "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9",
      "f58310d9-a9f6-439a-9e8d-f62e7b41a168",
      "b24988ac-6180-42a0-ab88-20f7382dd24c",
    ] : !strcontains(local.condicao, g)])
    error_message = "Nenhum papel de administração na lista atribuível."
  }
  assert {
    condition     = length(regexall("StringEqualsIgnoreCase \\{'ServicePrincipal'\\}", local.condicao)) == 2 && !strcontains(local.condicao, "'User'") && !strcontains(local.condicao, "'Group'")
    error_message = "Só ServicePrincipal, ao criar e ao remover."
  }
  assert {
    condition     = strcontains(local.condicao, "roleAssignments/write") && strcontains(local.condicao, "roleAssignments/delete")
    error_message = "A condição cobre criar e remover."
  }
}

run "pipeline_so_no_escopo_do_ambiente" {
  command = apply

  assert {
    condition     = alltrue([for k, a in azurerm_role_assignment.pipeline_contributor : a.scope == azurerm_resource_group.ambiente[k].id])
    error_message = "Contributor só no resource group do ambiente, nunca na assinatura."
  }
  assert {
    condition     = alltrue([for k, a in azurerm_role_assignment.pipeline_rbac_admin : a.scope == azurerm_resource_group.ambiente[k].id && a.condition != null])
    error_message = "RBAC Administrator só no resource group do ambiente e sempre com condição."
  }
  assert {
    condition     = azurerm_role_assignment.pipeline_estado.scope == azurerm_storage_container.estado.id
    error_message = "Dado do estado: papel só no container do estado."
  }
}
