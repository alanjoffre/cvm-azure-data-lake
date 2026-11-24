variable "localizacao" {
  description = "Região do estado remoto e dos resource groups."
  type        = string
  default     = "eastus2"
}

variable "ambientes" {
  description = "Ambientes que recebem um resource group e as permissões do pipeline."
  type        = set(string)
  default     = ["dev", "prod"]
}

variable "pipeline_principal_id" {
  description = "Object ID da identidade da service connection do Azure DevOps (federação). Por TF_VAR_pipeline_principal_id; não fica no repositório."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.pipeline_principal_id))
    error_message = "Informe o object ID (GUID) da identidade do pipeline."
  }
}
