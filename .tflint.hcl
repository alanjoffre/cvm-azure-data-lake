# tflint: regras do Terraform (todas) e as do provider azurerm (nomes, SKUs e tipos válidos).
#   tflint --init && tflint --recursive --config "$(pwd)/.tflint.hcl"

plugin "terraform" {
  enabled = true
  preset  = "all"
}

plugin "azurerm" {
  enabled = true
  version = "0.31.1"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}
