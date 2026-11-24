# Raiz: um ambiente por estado. O pipeline roda
#   terraform init -backend-config=...   (chave do estado = <ambiente>.tfstate)
#   terraform plan -var-file=ambientes/<ambiente>.tfvars
# e dev e prod diferem só nesse arquivo (D8).

module "ambiente" {
  source = "./modules/ambiente"

  ambiente              = var.ambiente
  resource_group_name   = var.resource_group_name
  localizacao           = var.localizacao
  emails_alerta         = var.emails_alerta
  orcamento_mensal      = var.orcamento_mensal
  funcao_max_instancias = var.funcao_max_instancias

  # A mesma lista que o bootstrap usa na condição do RBAC Administrator.
  papeis = jsondecode(file("${path.root}/papeis_atribuiveis.json"))
}
