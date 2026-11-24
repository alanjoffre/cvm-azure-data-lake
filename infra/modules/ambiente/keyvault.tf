# Key Vault só para o que é segredo (D11): a chave da Function que o ADF usa para chamá-la.
#
# O segredo NÃO é criado aqui. A chave da Function só existe depois que o código é publicado, e
# um segredo criado pelo Terraform ficaria gravado em texto no estado. A etapa de deploy
# cria a chave na Function e grava no cofre com a CLI, com o papel Key Vault Secrets Officer
# atribuído em rbac.tf. O estado guarda só o cofre.

# tflint-ignore: azurerm_resources_missing_prevent_destroy # o destroy encerra o ambiente inteiro (D8); a chave se grava de novo no deploy
resource "azurerm_key_vault" "kv" {
  #checkov:skip=CKV_AZURE_110: purge protection desligada; com ela o cofre ficaria 7+ dias sem poder ser apagado e o destroy do ambiente de teste não fecharia (D8).
  #checkov:skip=CKV_AZURE_42: idem: soft delete de 7 dias fica, a proteção contra purga não.
  #checkov:skip=CKV_AZURE_109: rede pública com RBAC; endpoint privado fica fora por custo (D12).
  #checkov:skip=CKV_AZURE_189: idem CKV_AZURE_109.
  #checkov:skip=CKV2_AZURE_32: endpoint privado fora por custo (D12).

  name                = local.nome_kv
  resource_group_name = data.azurerm_resource_group.ambiente.name
  location            = var.localizacao
  tenant_id           = data.azurerm_client_config.atual.tenant_id
  sku_name            = "standard"

  # Autorização por RBAC (papéis do Entra ID), não por política de acesso: a mesma forma de dar
  # e auditar acesso que o resto do ambiente usa.
  rbac_authorization_enabled = true

  # Mínimo permitido. Com o sufixo aleatório, um ambiente recriado ganha outro nome e não
  # colide com o cofre antigo ainda em soft delete.
  soft_delete_retention_days = 7
  purge_protection_enabled   = false

  public_network_access_enabled = true

  tags = local.tags
}
