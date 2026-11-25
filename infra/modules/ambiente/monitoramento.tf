# Operação sem Log Analytics (D12): um alerta por métrica para a falha do pipeline e um
# orçamento com aviso por e-mail.

resource "azurerm_monitor_action_group" "alerta" {
  name                = "ag-cvm-lake-${var.ambiente}"
  resource_group_name = data.azurerm_resource_group.ambiente.name
  short_name          = "cvm${var.ambiente}"

  dynamic "email_receiver" {
    for_each = var.emails_alerta

    content {
      name                    = "email-${email_receiver.key}"
      email_address           = email_receiver.value
      use_common_alert_schema = true
    }
  }

  tags = local.tags
}

# Qualquer execução de pipeline com falha. O ok=false da Function cai na atividade Fail, que
# derruba o pipeline: uma métrica cobre a validação e a reconciliação.
resource "azurerm_monitor_metric_alert" "falha_pipeline" {
  name                = "alerta-falha-pipeline-${var.ambiente}"
  resource_group_name = data.azurerm_resource_group.ambiente.name
  scopes              = [azurerm_data_factory.adf.id]
  description         = "Execução de pipeline do ADF com falha (carga do Informe Diário)."
  severity            = 1
  frequency           = "PT5M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.DataFactory/factories"
    metric_name      = "PipelineFailedRuns"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 0
  }

  action {
    action_group_id = azurerm_monitor_action_group.alerta.id
  }

  tags = local.tags
}

# O orçamento começa no primeiro dia do mês da criação (exigência da API). time_static grava a
# data no estado uma vez: o plano seguinte não tenta mudar o início.
resource "time_static" "orcamento" {}

resource "azurerm_consumption_budget_resource_group" "mensal" {
  name              = "orcamento-cvm-lake-${var.ambiente}"
  resource_group_id = data.azurerm_resource_group.ambiente.id
  amount            = var.orcamento_mensal
  time_grain        = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", time_static.orcamento.rfc3339)
  }

  # O orçamento só avisa, com horas de atraso; quem trava o gasto é o limite de gastos da conta
  # (runbook, passo 0). Dois avisos: metade gasta e previsão de estourar.
  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.emails_alerta
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Forecasted"
    contact_emails = var.emails_alerta
  }
}
