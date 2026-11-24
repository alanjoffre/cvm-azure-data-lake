# O Data Factory: só a fábrica e a identidade. Pipelines, datasets, linked services e gatilhos
# vêm do JSON do repositório, pelo ARM que o build exporta (D9).

resource "azurerm_data_factory" "adf" {
  #checkov:skip=CKV2_AZURE_15: chave gerenciada pela Microsoft; a fábrica não guarda credencial e o dado é público (D1).
  #checkov:skip=CKV_AZURE_103: sem integração Git de propósito; o JSON do repositório é a fonte e chega pelo ARM do build (D9).
  #checkov:skip=CKV_AZURE_104: rede pública; VNet gerenciada e endpoint privado ficam fora por custo (D12).

  name                = local.nome_adf
  resource_group_name = data.azurerm_resource_group.ambiente.name
  location            = var.localizacao

  # Identidade gerenciada do sistema: é com ela que o ADF lê e grava no lake e lê o segredo do
  # Key Vault. Nenhuma credencial guardada nos linked services.
  identity {
    type = "SystemAssigned"
  }

  # Sem github_configuration nem vsts_configuration: ligar o Git no ADF Studio traria de volta o
  # botão "Publish" e o branch adf_publish, e dev e prod deixariam de receber o mesmo ARM.

  # VNet gerenciada desligada: o Integration Runtime gerenciado com VNet cobra por hora ligado.
  managed_virtual_network_enabled = false
  public_network_enabled          = true

  tags = local.tags
}
