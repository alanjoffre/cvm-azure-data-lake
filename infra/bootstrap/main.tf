# Bootstrap: o que a identidade do pipeline não pode criar sozinha.
# 1. o estado remoto (resource group, conta sem chave compartilhada, container);
# 2. um resource group por ambiente;
# 3. as permissões do pipeline: dados só no container do estado; Contributor e RBAC
#    Administrator com condição só no resource group de cada ambiente.

data "azurerm_subscription" "atual" {}

resource "random_string" "sufixo" {
  length  = 6
  upper   = false
  special = false
}

locals {
  papeis = jsondecode(file("${path.module}/../papeis_atribuiveis.json"))

  # GUIDs dos papéis built-in (Microsoft Learn, "Azure built-in roles").
  guid_contributor = "b24988ac-6180-42a0-ab88-20f7382dd24c"
  guid_rbac_admin  = "f58310d9-a9f6-439a-9e8d-f62e7b41a168"
  guid_blob_dados  = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"

  # Condição do RBAC Administrator (Microsoft Learn, "Delegate Azure role assignment management
  # to others with conditions"). Duas metades, uma para criar e outra para remover:
  # - criar (write) só com um papel da lista e só para ServicePrincipal (@Request: o que se pede);
  # - remover (delete) só atribuição que tenha um papel da lista e seja de ServicePrincipal
  #   (@Resource: o que já existe). Sem a metade do delete, o destroy não removeria as
  #   atribuições; com ela, o pipeline não remove a atribuição de uma pessoa.
  # Assim o pipeline não se dá Owner, não dá papel a usuário nem a grupo, e não atribui nada
  # fora da lista que o módulo usa.
  lista_guids = join(", ", sort(values(local.papeis)))
  condicao    = <<-EOT
    (
     (
      !(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})
     )
     OR
     (
      @Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.lista_guids}}
      AND
      @Request[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {'ServicePrincipal'}
     )
    )
    AND
    (
     (
      !(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'})
     )
     OR
     (
      @Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {${local.lista_guids}}
      AND
      @Resource[Microsoft.Authorization/roleAssignments:PrincipalType] ForAnyOfAnyValues:StringEqualsIgnoreCase {'ServicePrincipal'}
     )
    )
  EOT

  tags = {
    projeto = "cvm-lake"
    origem  = "terraform-bootstrap"
  }
}

# --- 1. Estado remoto ---

resource "azurerm_resource_group" "estado" {
  name     = "rg-cvm-lake-tfstate"
  location = var.localizacao
  tags     = local.tags
}

resource "azurerm_storage_account" "estado" {
  #checkov:skip=CKV_AZURE_206: LRS basta; o versionamento abaixo protege contra estado corrompido ou apagado.
  #checkov:skip=CKV_AZURE_59: endpoint público com TLS e sem chave; endpoint privado fora por custo (D12).
  #checkov:skip=CKV2_AZURE_33: endpoint privado fora por custo (D12).
  #checkov:skip=CKV2_AZURE_1: chave gerenciada pela Microsoft.
  #checkov:skip=CKV_AZURE_33: a conta não usa filas.

  name                = "stcvmtfstate${random_string.sufixo.result}"
  resource_group_name = azurerm_resource_group.estado.name
  location            = azurerm_resource_group.estado.location

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  # Sem namespace hierárquico: o versionamento de blob, que guarda as versões anteriores do
  # estado, não existe em conta com HNS.
  is_hns_enabled = false

  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false

  min_tls_version                  = "TLS1_2"
  https_traffic_only_enabled       = true
  cross_tenant_replication_enabled = false
  local_user_enabled               = false

  blob_properties {
    # Cada gravação do estado vira uma versão: dá para voltar a um estado anterior.
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }

    container_delete_retention_policy {
      days = 30
    }
  }

  # O estado de dev e prod mora aqui. Nenhum plan pode apagar esta conta; para encerrar tudo,
  # o runbook (passo 12) remove esta linha depois do destroy dos ambientes.
  lifecycle {
    prevent_destroy = true
  }

  tags = local.tags
}

resource "azurerm_storage_container" "estado" {
  #checkov:skip=CKV2_AZURE_21: log de leitura de blob exige diagnóstico num destino de logs; Log Analytics fica fora por custo (D12).

  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.estado.id
  container_access_type = "private"

  lifecycle {
    prevent_destroy = true
  }
}

# O pipeline lê e grava o estado (com o lock por lease do blob) só neste container.
resource "azurerm_role_assignment" "pipeline_estado" {
  scope              = azurerm_storage_container.estado.id
  role_definition_id = "${data.azurerm_subscription.atual.id}/providers/Microsoft.Authorization/roleDefinitions/${local.guid_blob_dados}"
  principal_id       = var.pipeline_principal_id
  principal_type     = "ServicePrincipal"
}

# --- 2. Um resource group por ambiente ---

resource "azurerm_resource_group" "ambiente" {
  for_each = var.ambientes

  name     = "rg-cvm-lake-${each.key}"
  location = var.localizacao
  tags     = merge(local.tags, { ambiente = each.key })
}

# --- 3. Permissões do pipeline em cada ambiente ---

# Cria e apaga recursos, mas não atribui papel (Contributor não tem roleAssignments/write).
resource "azurerm_role_assignment" "pipeline_contributor" {
  for_each = azurerm_resource_group.ambiente

  scope              = each.value.id
  role_definition_id = "${data.azurerm_subscription.atual.id}/providers/Microsoft.Authorization/roleDefinitions/${local.guid_contributor}"
  principal_id       = var.pipeline_principal_id
  principal_type     = "ServicePrincipal"
}

# Atribui papel, mas só os da lista e só a identidades de serviço (condição acima).
resource "azurerm_role_assignment" "pipeline_rbac_admin" {
  for_each = azurerm_resource_group.ambiente

  scope              = each.value.id
  role_definition_id = "${data.azurerm_subscription.atual.id}/providers/Microsoft.Authorization/roleDefinitions/${local.guid_rbac_admin}"
  principal_id       = var.pipeline_principal_id
  principal_type     = "ServicePrincipal"
  condition          = local.condicao
  condition_version  = "2.0"
}
