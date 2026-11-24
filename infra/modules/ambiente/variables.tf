variable "ambiente" {
  description = "Nome do ambiente. Entra nos nomes e nas tags."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.ambiente)
    error_message = "O ambiente é dev ou prod."
  }
}

variable "resource_group_name" {
  description = "Resource group do ambiente, criado pelo bootstrap (a identidade do pipeline não cria resource group)."
  type        = string
}

variable "localizacao" {
  description = "Região dos recursos. Precisa oferecer o plano Flex Consumption."
  type        = string
}

variable "papeis" {
  description = "Papéis que o pipeline pode atribuir (nome → GUID), lidos de papeis_atribuiveis.json. A mesma lista monta a condição do bootstrap."
  type        = map(string)
}

variable "emails_alerta" {
  description = "Quem recebe o alerta de falha e o aviso do orçamento. Vem do pipeline (TF_VAR_emails_alerta), nunca do repositório."
  type        = list(string)

  validation {
    condition     = length(var.emails_alerta) > 0 && alltrue([for e in var.emails_alerta : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", e))])
    error_message = "Informe ao menos um e-mail válido."
  }
}

variable "orcamento_mensal" {
  description = "Teto do orçamento mensal do resource group, na moeda da assinatura."
  type        = number

  validation {
    condition     = var.orcamento_mensal > 0
    error_message = "O orçamento precisa ser maior que zero."
  }
}

variable "retencao_bronze_dias" {
  description = "Dias que uma versão da bronze fica guardada (D5)."
  type        = number
  default     = 90
}

variable "retencao_staging_dias" {
  description = "Dias até apagar o que sobrou na staging (carga que falhou antes do Delete final)."
  type        = number
  default     = 7
}

variable "funcao_memoria_mb" {
  description = "Memória de cada instância da Function (Flex Consumption: 512, 2048 ou 4096)."
  type        = number
  default     = 2048

  validation {
    condition     = contains([512, 2048, 4096], var.funcao_memoria_mb)
    error_message = "O Flex Consumption aceita 512, 2048 ou 4096 MB."
  }
}

variable "funcao_max_instancias" {
  description = "Teto de instâncias da Function. Trava de custo e de concorrência no lake."
  type        = number
  default     = 40
}

variable "tags" {
  description = "Tags extras, somadas às do módulo."
  type        = map(string)
  default     = {}
}
