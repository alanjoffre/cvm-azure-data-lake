#!/usr/bin/env bash
# Roda o arm-ttk (o kit de testes de template ARM da Microsoft) sobre o ARM que ci/adf/validar.sh
# exporta. Precisa de PowerShell 7 (pwsh): roda no runner Linux do GitHub e no agente próprio.
#   ci/adf/armttk.sh [pasta-do-arm]       # padrão: saida/arm
set -euo pipefail
raiz_repo="$(cd "$(dirname "$0")/../.." && pwd)"
arm="$(realpath "${1:-$raiz_repo/saida/arm}")"
template="$arm/ARMTemplateForFactory.json"
[[ -f "$template" ]] || { echo "sem $template: rode ci/adf/validar.sh antes" >&2; exit 1; }

# Fixado pelo commit da tag 20260213 (a última release), não pelo nome da tag, que se move.
versao="20260213"
commit="0ec9a41a4503e970a0ec8efb0cd08415cc172175"
kit="$raiz_repo/saida/arm-ttk-$versao"
if [[ ! -f "$kit/arm-ttk/arm-ttk.psd1" ]]; then
  rm -rf "$kit"
  git init -q "$kit"
  git -C "$kit" fetch -q --depth 1 https://github.com/Azure/arm-ttk.git "$commit"
  git -C "$kit" checkout -q FETCH_HEAD
fi
[[ "$(git -C "$kit" rev-parse HEAD)" == "$commit" ]] || { echo "arm-ttk fora do commit fixado" >&2; exit 1; }

# Os quatro testes pulados reprovam o que o gerador da Microsoft escreve, não o que este
# repositório controla; qualquer outro teste reprovado derruba o CI:
#   DeploymentTemplate-Schema-Is-Correct            o export grava o $schema com http://
#   DeploymentTemplate-Must-Not-Contain-Hardcoded-Uri  pelo mesmo $schema: a exceção da regra
#                                                   só reconhece https://schema.management...
#   Template-Should-Not-Contain-Blanks              o export grava userProperties, dependsOn e
#                                                   annotations vazios em cada recurso
#   DependsOn-Best-Practices                        o export grava todo dependsOn como
#                                                   [concat(variables('factoryId'), '/...')]
# O que as duas regras de conteúdo cobririam, tests/test_adf.py confere no mesmo ARM: nenhum
# endereço fixo da Azure nos recursos e todo dependsOn apontando para um recurso do template.
# Stop só no Import-Module: dentro do Test-AzTemplate, cada reprovação é um Write-Error, e com
# Stop a primeira interromperia o kit antes de listar as outras.
# Os caminhos vão por variável de ambiente: no pwsh, o que vem depois de -Command entra no
# próprio comando.
# shellcheck disable=SC2016 # o $ é do PowerShell, não do bash
ARMTTK_KIT="$kit" ARMTTK_TEMPLATE="$template" pwsh -NoProfile -NonInteractive -Command '
  $ErrorActionPreference = "Stop"
  Import-Module (Join-Path $env:ARMTTK_KIT "arm-ttk/arm-ttk.psd1")
  $ErrorActionPreference = "Continue"
  $pulados = @("DeploymentTemplate-Schema-Is-Correct", "DeploymentTemplate-Must-Not-Contain-Hardcoded-Uri",
               "Template-Should-Not-Contain-Blanks", "DependsOn-Best-Practices")
  $r = @(Test-AzTemplate -TemplatePath $env:ARMTTK_TEMPLATE -Skip $pulados)
  $r | Format-Table -AutoSize Name, Passed | Out-String -Width 200 | Write-Host
  $reprovados = @($r | Where-Object { -not $_.Passed })
  if ($r.Count -eq 0) { Write-Host "nenhum teste rodou"; exit 1 }
  if ($reprovados.Count -gt 0) { $reprovados | Format-List Name, Errors | Out-String | Write-Host; exit 1 }
  Write-Host ("arm-ttk: {0} testes aprovados, 4 pulados." -f $r.Count)
'
