# Testes do módulo sem conta na Azure: o provider azurerm é simulado (mock_provider) e os data
# sources devolvem valores fixos. O que se testa é o desenho: o que o plano pediria à Azure.
#
#   cd infra/modules/ambiente && terraform init -backend=false && terraform test

mock_provider "azurerm" {
  # IDs no formato do ARM, para os recursos que validam o ID de outro recurso no apply simulado.
  mock_resource "azurerm_service_plan" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-dev/providers/Microsoft.Web/serverFarms/asp-teste"
    }
  }
  mock_resource "azurerm_data_factory" {
    defaults = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-dev/providers/Microsoft.DataFactory/factories/adf-teste"
      identity = { principal_id = "11111111-1111-1111-1111-111111111111", tenant_id = "22222222-2222-2222-2222-222222222222" }
    }
  }
  mock_resource "azurerm_function_app_flex_consumption" {
    defaults = {
      identity = { principal_id = "33333333-3333-3333-3333-333333333333", tenant_id = "22222222-2222-2222-2222-222222222222" }
    }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-dev/providers/Microsoft.KeyVault/vaults/kv-teste"
    }
  }
  mock_resource "azurerm_monitor_action_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-dev/providers/Microsoft.Insights/actionGroups/ag-teste"
    }
  }

  override_data {
    target = data.azurerm_client_config.atual
    values = {
      tenant_id       = "22222222-2222-2222-2222-222222222222"
      object_id       = "44444444-4444-4444-4444-444444444444"
      subscription_id = "00000000-0000-0000-0000-000000000000"
    }
  }
  override_data {
    target = data.azurerm_subscription.atual
    values = {
      id              = "/subscriptions/00000000-0000-0000-0000-000000000000"
      subscription_id = "00000000-0000-0000-0000-000000000000"
    }
  }
  override_data {
    target = data.azurerm_resource_group.ambiente
    values = {
      id       = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-dev"
      name     = "rg-cvm-lake-dev"
      location = "eastus2"
    }
  }
}

# As duas contas com IDs diferentes, para o teste distinguir o escopo do lake do escopo do host.
override_resource {
  target = azurerm_storage_account.lake
  values = {
    id                    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-dev/providers/Microsoft.Storage/storageAccounts/stlake"
    primary_blob_endpoint = "https://stlake.blob.core.windows.net/"
  }
}

override_resource {
  target = azurerm_storage_account.host
  values = {
    id                    = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-cvm-lake-dev/providers/Microsoft.Storage/storageAccounts/sthost"
    primary_blob_endpoint = "https://sthost.blob.core.windows.net/"
  }
}

variables {
  ambiente            = "dev"
  resource_group_name = "rg-cvm-lake-dev"
  localizacao         = "eastus2"
  emails_alerta       = ["alerta@example.com"]
  orcamento_mensal    = 10
  papeis              = jsondecode(file("../../papeis_atribuiveis.json"))
}

run "lake_sem_chave_e_sem_acesso_anonimo" {
  command = plan

  assert {
    condition     = azurerm_storage_account.lake.is_hns_enabled
    error_message = "O lake precisa do namespace hierárquico (ADLS Gen2)."
  }
  assert {
    condition     = !azurerm_storage_account.lake.shared_access_key_enabled && azurerm_storage_account.lake.default_to_oauth_authentication
    error_message = "A chave compartilhada do lake fica desligada; o acesso é por Entra ID."
  }
  assert {
    condition     = !azurerm_storage_account.lake.allow_nested_items_to_be_public
    error_message = "Nenhum blob anônimo no lake."
  }
  assert {
    condition     = azurerm_storage_account.lake.min_tls_version == "TLS1_2" && azurerm_storage_account.lake.https_traffic_only_enabled
    error_message = "TLS 1.2 mínimo e só HTTPS."
  }
}

run "uma_camada_por_container_todas_privadas" {
  command = plan

  assert {
    condition     = toset(keys(azurerm_storage_container.camada)) == toset(["bronze", "staging", "prata", "ouro", "controle"])
    error_message = "As camadas são bronze, staging, prata, ouro e controle."
  }
  assert {
    condition     = alltrue([for c in azurerm_storage_container.camada : c.container_access_type == "private"])
    error_message = "Todo container é privado."
  }
}

run "ciclo_de_vida_da_bronze_e_da_staging" {
  command = plan

  assert {
    condition = {
      for r in azurerm_storage_management_policy.lake.rule :
      one(r.filters[0].prefix_match) => r.actions[0].base_blob[0].delete_after_days_since_creation_greater_than
    } == { "bronze/" = 90, "staging/" = 7 }
    error_message = "A bronze guarda 90 dias (D5); a staging, 7."
  }
}

run "adf_com_identidade_e_sem_git" {
  command = plan

  assert {
    condition     = azurerm_data_factory.adf.identity[0].type == "SystemAssigned"
    error_message = "O ADF usa a identidade gerenciada do sistema."
  }
  assert {
    condition     = length(azurerm_data_factory.adf.github_configuration) == 0 && length(azurerm_data_factory.adf.vsts_configuration) == 0
    error_message = "Sem integração Git no ADF: o JSON do repositório chega pelo ARM (D9)."
  }
  assert {
    condition     = !azurerm_data_factory.adf.managed_virtual_network_enabled
    error_message = "VNet gerenciada fica fora por custo (D12)."
  }
}

run "funcao_flex_com_host_proprio_por_identidade" {
  command = apply

  assert {
    condition     = azurerm_service_plan.funcao.sku_name == "FC1" && azurerm_service_plan.funcao.os_type == "Linux"
    error_message = "A Function roda no Flex Consumption (FC1, Linux)."
  }
  assert {
    condition     = azurerm_function_app_flex_consumption.funcao.runtime_name == "python" && azurerm_function_app_flex_consumption.funcao.runtime_version == "3.12"
    error_message = "Python 3.12, a versão do projeto."
  }
  assert {
    condition     = azurerm_function_app_flex_consumption.funcao.storage_authentication_type == "SystemAssignedIdentity"
    error_message = "O pacote é lido pela identidade, sem chave de conta."
  }
  assert {
    condition     = azurerm_function_app_flex_consumption.funcao.app_settings["AzureWebJobsStorage__credential"] == "managedidentity" && azurerm_function_app_flex_consumption.funcao.app_settings["AzureWebJobsStorage"] == ""
    error_message = "O host fala com a própria conta por identidade, sem string de conexão."
  }
  assert {
    condition     = azurerm_storage_account.host.name != azurerm_storage_account.lake.name && !azurerm_storage_account.host.shared_access_key_enabled
    error_message = "O host tem conta própria, separada do lake e também sem chave."
  }
  assert {
    condition     = azurerm_function_app_flex_consumption.funcao.app_settings["LAKE_CONTA"] == azurerm_storage_account.lake.name
    error_message = "A Function recebe a conta do lake por app setting."
  }
  assert {
    condition     = azurerm_function_app_flex_consumption.funcao.https_only && !azurerm_function_app_flex_consumption.funcao.webdeploy_publish_basic_authentication_enabled
    error_message = "Só HTTPS e sem publicação por usuário e senha."
  }
}

run "key_vault_por_rbac_sem_segredo_no_estado" {
  command = plan

  assert {
    condition     = azurerm_key_vault.kv.rbac_authorization_enabled
    error_message = "O cofre usa RBAC, não política de acesso (D11)."
  }
  assert {
    condition     = !azurerm_key_vault.kv.purge_protection_enabled && azurerm_key_vault.kv.soft_delete_retention_days == 7
    error_message = "Soft delete de 7 dias, sem purge protection: o destroy precisa fechar (D8)."
  }
}

run "permissoes_minimas" {
  command = apply

  assert {
    condition     = alltrue([for a in azurerm_role_assignment.papel : a.principal_type == "ServicePrincipal"])
    error_message = "Toda atribuição é para identidade de serviço (a condição do bootstrap só permite essas)."
  }
  assert {
    condition     = alltrue([for a in azurerm_role_assignment.papel : contains(values(var.papeis), element(split("/", a.role_definition_id), length(split("/", a.role_definition_id)) - 1))])
    error_message = "Todo papel atribuído está na lista do bootstrap."
  }
  assert {
    condition = toset([
      for k, a in azurerm_role_assignment.papel : output.papeis_atribuidos[k] if a.scope == azurerm_storage_account.lake.id
    ]) == toset(["Storage Blob Data Contributor"])
    error_message = "No lake, só Storage Blob Data Contributor (nada de Owner)."
  }
  assert {
    condition     = output.papeis_atribuidos == { adf_lake = "Storage Blob Data Contributor", adf_kv = "Key Vault Secrets User", funcao_lake = "Storage Blob Data Contributor", funcao_host_blob = "Storage Blob Data Owner", funcao_host_tabela = "Storage Table Data Contributor", deploy_kv = "Key Vault Secrets Officer" }
    error_message = "A lista de atribuições mudou: revise a seção 7 da arquitetura e a condição do bootstrap."
  }
  assert {
    condition     = azurerm_role_assignment.papel["adf_lake"].principal_id == "11111111-1111-1111-1111-111111111111" && azurerm_role_assignment.papel["funcao_lake"].principal_id == "33333333-3333-3333-3333-333333333333" && azurerm_role_assignment.papel["deploy_kv"].principal_id == "44444444-4444-4444-4444-444444444444"
    error_message = "Cada papel vai para a identidade certa: ADF, Function e pipeline."
  }
}

run "nomes_dentro_dos_limites_da_azure" {
  command = apply

  assert {
    condition     = alltrue([for n in [azurerm_storage_account.lake.name, azurerm_storage_account.host.name] : can(regex("^[a-z0-9]{3,24}$", n))])
    error_message = "Conta de armazenamento: 3 a 24 caracteres, minúsculas e números."
  }
  assert {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{1,22}[a-zA-Z0-9]$", azurerm_key_vault.kv.name))
    error_message = "Key Vault: 3 a 24 caracteres, começa com letra."
  }
}

run "orcamento_com_aviso_por_email" {
  command = apply

  assert {
    condition     = endswith(azurerm_consumption_budget_resource_group.mensal.time_period[0].start_date, "-01T00:00:00Z")
    error_message = "O orçamento começa no primeiro dia do mês (exigência da API)."
  }
  assert {
    condition     = alltrue([for n in azurerm_consumption_budget_resource_group.mensal.notification : n.contact_emails == tolist(["alerta@example.com"])])
    error_message = "Os avisos vão para os e-mails da variável."
  }
  assert {
    condition     = toset([for n in azurerm_consumption_budget_resource_group.mensal.notification : n.threshold_type]) == toset(["Actual", "Forecasted"])
    error_message = "Um aviso pelo gasto real e outro pela previsão."
  }
  assert {
    condition     = azurerm_monitor_metric_alert.falha_pipeline.criteria[0].metric_name == "PipelineFailedRuns" && azurerm_monitor_metric_alert.falha_pipeline.criteria[0].threshold == 0
    error_message = "Alerta na primeira execução de pipeline com falha."
  }
}

run "ambiente_desconhecido_e_recusado" {
  command = plan

  variables {
    ambiente = "hml"
  }

  expect_failures = [var.ambiente]
}

run "sem_email_e_recusado" {
  command = plan

  variables {
    emails_alerta = []
  }

  expect_failures = [var.emails_alerta]
}
