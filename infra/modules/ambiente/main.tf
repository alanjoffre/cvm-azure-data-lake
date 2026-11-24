# Um ambiente inteiro (D8): lake, ADF, Function com o armazenamento do host, Key Vault,
# permissões, alerta e orçamento. Dev e prod chamam este mesmo módulo; muda só o .tfvars.

data "azurerm_client_config" "atual" {}

data "azurerm_subscription" "atual" {}

# O resource group vem do bootstrap: a identidade do pipeline tem Contributor só nele e não
# pode criar outro.
data "azurerm_resource_group" "ambiente" {
  name = var.resource_group_name
}

# Sufixo dos nomes globais (conta de armazenamento, Key Vault, ADF, Function). Fica no estado:
# o nome é estável entre execuções e muda só se o ambiente for destruído e recriado, o que
# também evita colidir com um Key Vault ainda em soft delete.
resource "random_string" "sufixo" {
  length  = 6
  upper   = false
  special = false
}

locals {
  sufixo = random_string.sufixo.result

  # Contas de armazenamento: 3 a 24 caracteres, só minúsculas e números.
  nome_lake = "stcvmlake${var.ambiente}${local.sufixo}"
  nome_host = "stcvmfunc${var.ambiente}${local.sufixo}"

  nome_adf    = "adf-cvm-lake-${var.ambiente}-${local.sufixo}"
  nome_funcao = "func-cvm-lake-${var.ambiente}-${local.sufixo}"
  nome_plano  = "asp-cvm-lake-${var.ambiente}"
  nome_kv     = "kv-cvm-${var.ambiente}-${local.sufixo}"

  tags = merge(var.tags, {
    projeto  = "cvm-lake"
    ambiente = var.ambiente
    origem   = "terraform"
  })
}
