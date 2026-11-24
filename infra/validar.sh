#!/usr/bin/env bash
# Valida o Terraform sem conta na Azure: formato, validate, tflint, Checkov e terraform test
# (provider simulado). O mesmo script roda na máquina e no CI.
#   infra/validar.sh
set -euo pipefail
cd "$(dirname "$0")"
raiz_repo="$(cd .. && pwd)"

echo "== fmt"
terraform fmt -recursive -check -diff

for pasta in . bootstrap modules/ambiente; do
  echo "== init + validate: $pasta"
  terraform -chdir="$pasta" init -backend=false -input=false -no-color >/dev/null
  terraform -chdir="$pasta" validate -no-color
done

echo "== tflint"
tflint --init --config "$raiz_repo/.tflint.hcl" >/dev/null
tflint --recursive --config "$raiz_repo/.tflint.hcl"

echo "== checkov"
uvx checkov -d . --framework terraform --compact --quiet

echo "== terraform test: módulo do ambiente"
terraform -chdir=modules/ambiente test -no-color

echo "== terraform test: bootstrap"
terraform -chdir=bootstrap test -no-color

for ambiente in dev prod; do
  echo "== terraform test: raiz com ambientes/$ambiente.tfvars"
  terraform test -no-color -var-file="ambientes/$ambiente.tfvars"
done

echo "Terraform validado."
