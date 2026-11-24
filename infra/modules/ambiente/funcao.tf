# A Function (D2): Python no plano Flex Consumption, com uma conta de armazenamento só para o
# host, separada do lake.

# Conta do host: o runtime guarda nela os locks e os eventos de diagnóstico, e o Flex guarda o
# pacote publicado num container. Separada do lake para que nada do runtime se misture ao dado
# e para que a permissão de dono (Blob Data Owner) fique longe do lake.
# tflint-ignore: azurerm_resources_missing_prevent_destroy # o destroy encerra o ambiente inteiro (D8); o pacote se publica de novo no deploy
resource "azurerm_storage_account" "host" {
  #checkov:skip=CKV_AZURE_206: LRS basta para o host; o pacote se publica de novo pelo pipeline.
  #checkov:skip=CKV_AZURE_59: endpoint público com TLS e sem chave; endpoint privado fora por custo (D12).
  #checkov:skip=CKV2_AZURE_33: endpoint privado fora por custo (D12).
  #checkov:skip=CKV2_AZURE_1: chave gerenciada pela Microsoft; a conta não guarda dado de negócio.
  #checkov:skip=CKV_AZURE_33: gatilho só HTTP; o host não usa filas.
  #checkov:skip=CKV2_AZURE_38: o host não guarda nada que precise de soft delete; o pacote vem do pipeline.

  name                = local.nome_host
  resource_group_name = data.azurerm_resource_group.ambiente.name
  location            = var.localizacao

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  # Também sem chave: o host fala com a conta pela identidade gerenciada da Function.
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false

  min_tls_version                  = "TLS1_2"
  https_traffic_only_enabled       = true
  cross_tenant_replication_enabled = false
  local_user_enabled               = false

  tags = local.tags
}

# Onde o Flex Consumption guarda o pacote (.zip) que o deploy publica.
# tflint-ignore: azurerm_resources_missing_prevent_destroy # o destroy encerra o ambiente inteiro (D8); o pacote se publica de novo no deploy
resource "azurerm_storage_container" "pacote" {
  #checkov:skip=CKV2_AZURE_21: log de leitura de blob exige diagnóstico num destino de logs; Log Analytics fica fora por custo (D12).

  name                  = "pacote-funcao"
  storage_account_id    = azurerm_storage_account.host.id
  container_access_type = "private"
}

# FC1 é o SKU do Flex Consumption: cobra por execução e por memória × tempo, escala a zero.
resource "azurerm_service_plan" "funcao" {
  #checkov:skip=CKV_AZURE_212: o Flex Consumption escala a zero e sobe instância por demanda; mínimo fixo seria custo parado.
  #checkov:skip=CKV_AZURE_225: redundância de zona cobra instâncias sempre ligadas; a carga é diária e reexecutável (D3).

  name                = local.nome_plano
  resource_group_name = data.azurerm_resource_group.ambiente.name
  location            = var.localizacao
  os_type             = "Linux"
  sku_name            = "FC1"

  tags = local.tags
}

resource "azurerm_function_app_flex_consumption" "funcao" {
  #checkov:skip=CKV_AZURE_221: rede pública; o ADF chama pela internet com a chave do Key Vault, e endpoint privado fica fora por custo (D12).

  name                = local.nome_funcao
  resource_group_name = data.azurerm_resource_group.ambiente.name
  location            = var.localizacao
  service_plan_id     = azurerm_service_plan.funcao.id

  runtime_name    = "python"
  runtime_version = "3.12"

  # O pacote fica no container do host, lido pela identidade do sistema: nenhuma chave de conta
  # no app.
  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.host.primary_blob_endpoint}${azurerm_storage_container.pacote.name}"
  storage_authentication_type = "SystemAssignedIdentity"

  # 2 GB por instância: o mês (~540 mil linhas, ~54 MB de CSV) cabe com folga no DuckDB em
  # memória. O teto de instâncias trava custo e concorrência no lake.
  instance_memory_in_mb  = var.funcao_memoria_mb
  maximum_instance_count = var.funcao_max_instancias

  https_only                                     = true
  webdeploy_publish_basic_authentication_enabled = false
  public_network_access_enabled                  = true

  identity {
    type = "SystemAssigned"
  }

  app_settings = {
    # Conexão do host com a própria conta por identidade (Microsoft Learn, "identity-based
    # connections"). Sem __clientId, o runtime usa a identidade do sistema.
    "AzureWebJobsStorage__accountName" = azurerm_storage_account.host.name
    "AzureWebJobsStorage__credential"  = "managedidentity"

    # O provider (4.x) grava AzureWebJobsStorage como string de conexão com chave vazia mesmo
    # com SystemAssignedIdentity (issues 29693 e 33211 do terraform-provider-azurerm, abertas).
    # A string vazia é o contorno descrito nas issues: o runtime ignora o valor vazio e usa as
    # duas chaves acima. Conferir na execução (testes.md, "Só na Azure").
    "AzureWebJobsStorage" = ""

    # O que o código da Function lê: a conta do lake (o SDK monta o endpoint dfs a partir dela)
    # e o ambiente.
    "LAKE_CONTA"    = azurerm_storage_account.lake.name
    "LAKE_AMBIENTE" = var.ambiente
  }

  site_config {
    minimum_tls_version     = "1.2"
    scm_minimum_tls_version = "1.2"
    http2_enabled           = true
  }

  tags = local.tags
}
