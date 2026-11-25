# O lake: ADLS Gen2 com um container por camada (arquitetura, seção 4).

# tflint-ignore: azurerm_resources_missing_prevent_destroy # o destroy encerra o ambiente inteiro (D8); o dado se baixa de novo da CVM
resource "azurerm_storage_account" "lake" {
  #checkov:skip=CKV_AZURE_206: LRS basta; o dado é público e se baixa de novo da CVM (D1).
  #checkov:skip=CKV_AZURE_59: endpoint público com TLS e sem chave; endpoint privado fica fora por custo (D12).
  #checkov:skip=CKV2_AZURE_33: endpoint privado fora por custo (D12).
  #checkov:skip=CKV2_AZURE_1: chave gerenciada pela Microsoft; o dado é público (D1).
  #checkov:skip=CKV_AZURE_33: a conta não usa filas; o log de filas não se aplica.
  #checkov:skip=CKV2_AZURE_38: soft delete de blob ligado abaixo; o de container fica fora porque o destroy precisa apagar tudo (D8).

  name                = local.nome_lake
  resource_group_name = data.azurerm_resource_group.ambiente.name
  location            = var.localizacao

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  # Namespace hierárquico: é o que faz da conta um ADLS Gen2 (pastas de verdade e rename
  # atômico, que a Function usa para regravar o manifesto e o ouro sem truncar o arquivo vigente).
  is_hns_enabled = true

  # Sem chave compartilhada: só identidade (Entra ID) acessa o dado. O provider também
  # precisa falar com a conta por Entra ID (storage_use_azuread no provider da raiz).
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true

  # Nenhum blob anônimo, nem a pedido de quem cria um container.
  allow_nested_items_to_be_public = false

  min_tls_version                  = "TLS1_2"
  https_traffic_only_enabled       = true
  cross_tenant_replication_enabled = false
  local_user_enabled               = false

  blob_properties {
    # Rede de proteção contra apagar sem querer: 7 dias para recuperar um blob.
    delete_retention_policy {
      days = 7
    }
  }

  tags = local.tags
}

# Um container por camada. Cada um é criado pelo plano de controle (storage_account_id), sem passar pelo
# plano de dados: funciona com a chave compartilhada desligada.
# tflint-ignore: azurerm_resources_missing_prevent_destroy # o destroy encerra o ambiente inteiro (D8); o dado se baixa de novo da CVM
resource "azurerm_storage_container" "camada" {
  #checkov:skip=CKV2_AZURE_21: log de leitura de blob exige diagnóstico num destino de logs; Log Analytics fica fora por custo (D12).

  for_each = toset(["bronze", "staging", "prata", "ouro", "controle"])

  name                  = each.key
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# Ciclo de vida: a bronze guarda cada versão por 90 dias (D5); a staging é transitória, e o que
# sobra nela é carga que falhou antes do Delete do passo 7.
resource "azurerm_storage_management_policy" "lake" {
  storage_account_id = azurerm_storage_account.lake.id

  rule {
    name    = "bronze-${var.retencao_bronze_dias}-dias"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["bronze/"]
    }

    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = var.retencao_bronze_dias
      }
    }
  }

  rule {
    name    = "staging-${var.retencao_staging_dias}-dias"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["staging/"]
    }

    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = var.retencao_staging_dias
      }
    }
  }
}
