variable "ambiente" {
  description = "dev ou prod. Vem do arquivo ambientes/<ambiente>.tfvars."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group do ambiente, criado pelo bootstrap."
  type        = string
}

variable "localizacao" {
  description = "Região dos recursos."
  type        = string
}

variable "emails_alerta" {
  description = "Destinatários do alerta e do orçamento. Só por TF_VAR_emails_alerta (variável secreta do pipeline); nunca em arquivo do repositório."
  type        = list(string)
  sensitive   = true
}

variable "orcamento_mensal" {
  description = "Teto do orçamento mensal do ambiente."
  type        = number
}

variable "funcao_max_instancias" {
  description = "Teto de instâncias da Function."
  type        = number
  default     = 40
}
