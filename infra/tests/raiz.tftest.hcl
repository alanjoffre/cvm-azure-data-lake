# A raiz com o arquivo de cada ambiente, sem conta (provider simulado):
#
#   terraform test -var-file=ambientes/dev.tfvars
#   terraform test -var-file=ambientes/prod.tfvars
#
# Prova que o .tfvars do ambiente chega ao módulo e que o plano fecha.

mock_provider "azurerm" {
  override_data {
    target = module.ambiente.data.azurerm_client_config.atual
    values = {
      tenant_id       = "22222222-2222-2222-2222-222222222222"
      object_id       = "44444444-4444-4444-4444-444444444444"
      subscription_id = "00000000-0000-0000-0000-000000000000"
    }
  }
  override_data {
    target = module.ambiente.data.azurerm_subscription.atual
    values = {
      id              = "/subscriptions/00000000-0000-0000-0000-000000000000"
      subscription_id = "00000000-0000-0000-0000-000000000000"
    }
  }
  override_data {
    target = module.ambiente.data.azurerm_resource_group.ambiente
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-teste"
    }
  }
}

variables {
  # Nos testes, um endereço de exemplo; no pipeline, TF_VAR_emails_alerta.
  emails_alerta = ["alerta@example.com"]
}

run "o_ambiente_do_tfvars_chega_ao_modulo" {
  command = plan

  assert {
    condition     = var.resource_group_name == "rg-cvm-lake-${var.ambiente}"
    error_message = "O resource group do .tfvars é o que o bootstrap cria para o ambiente."
  }
  assert {
    condition     = length(output.papeis_atribuidos) == 6
    error_message = "O ambiente atribui os 6 papéis da seção 7 da arquitetura."
  }
}
