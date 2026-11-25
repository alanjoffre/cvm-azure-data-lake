"""Os scripts do CI/CD, sem conta: pacote da Function, parâmetros do ARM e o deploy com CLIs falsas.

``az`` e ``terraform`` falsos no PATH registram cada chamada e guardam o estado numa pasta: o
teste confere o que o script pede à CLI e, no caso do segredo, o que ele nunca deixa escapar.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from test_adf import ARM, PARAMETROS_DO_DEPLOY, sem_arm

RAIZ = Path(__file__).resolve().parents[1]


def _modulo(caminho: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(Path(caminho).stem, RAIZ / caminho)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


empacotar = _modulo("ci/funcao/empacotar.py")
parametros_arm = _modulo("ci/deploy/parametros_arm.py")
variaveis = _modulo("ci/deploy/variaveis.py")

SAIDAS_TF = set(re.findall(r'^output "(\w+)"', (RAIZ / "infra/outputs.tf").read_text(), re.M))


# --- pacote da Function -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def requisitos() -> str:
    return str(empacotar.requisitos())


def test_pacote_tem_o_layout_do_python_na_raiz_e_so_o_codigo(
    tmp_path: Path, requisitos: str
) -> None:
    sha = empacotar.empacotar(RAIZ, tmp_path / "f.zip", requisitos)
    nomes = zipfile.ZipFile(tmp_path / "f.zip").namelist()
    assert {"function_app.py", "host.json", "requirements.txt"} <= set(nomes)
    assert "cvm_lake/funcao/adls.py" in nomes and "cvm_lake/funcao/entrada.py" in nomes
    assert all(n.startswith("cvm_lake/") or "/" not in n for n in nomes)
    assert not [n for n in nomes if "__pycache__" in n or n.endswith(".pyc") or "test" in n]
    # Reprodutível: o mesmo código dá o mesmo sha256.
    assert empacotar.empacotar(RAIZ, tmp_path / "g.zip", requisitos) == sha
    assert hashlib.sha256((tmp_path / "g.zip").read_bytes()).hexdigest() == sha


def test_requirements_sai_do_lock_com_hash_e_inclui_o_sdk(requisitos: str) -> None:
    pacotes = {linha.split("==")[0] for linha in requisitos.splitlines() if "==" in linha}
    assert {"azure-functions", "azure-identity", "azure-storage-file-datalake", "duckdb"} <= pacotes
    assert not {"pytest", "ruff", "mypy"} & pacotes
    assert "--hash=sha256:" in requisitos
    # O projeto vai como código no zip, não como linha do requirements (só aparece em "# via").
    assert not [
        x for x in requisitos.splitlines() if x.startswith(("-e", ".", "cvm-azure-data-lake"))
    ]


def test_function_app_importa_do_pacote_extraido(tmp_path: Path, requisitos: str) -> None:
    empacotar.empacotar(RAIZ, tmp_path / "f.zip", requisitos)
    pasta = tmp_path / "wwwroot"
    zipfile.ZipFile(tmp_path / "f.zip").extractall(pasta)
    codigo = (
        "import function_app, cvm_lake; "
        "print(cvm_lake.__file__); "
        "print(sorted(f.get_function_name() for f in function_app.app.get_functions()))"
    )
    saida = subprocess.run(
        [sys.executable, "-c", codigo], cwd=pasta, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert saida[0].startswith(str(pasta))
    assert saida[1] == "['reconciliar', 'validar']"


# --- parâmetros do ARM --------------------------------------------------------------------------


def _parametros_exemplo() -> dict[str, Any]:
    valores: dict[str, Any] = {
        "factoryName": "adf-cvm-lake",
        "ls_adls_lake_properties_typeProperties_url": "https://lake.definido-no-deploy.invalid/",
        "ls_kv_properties_typeProperties_baseUrl": "https://kv.definido-no-deploy.invalid/",
        "ls_funcao_properties_typeProperties_functionAppUrl": "https://funcao.definido-no-deploy.invalid",
        "ls_http_cvm_properties_typeProperties_url": "https://dados.cvm.gov.br/",
        "tr_diario_properties_pl_inf_diario_parameters_de": 0,
    }
    return {
        "$schema": "https://schema/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {n: {"value": v} for n, v in valores.items()},
    }


SAIDAS = {
    "adf_nome": {"value": "adf-cvm-lake-dev-x1"},
    "lake_dfs_endpoint": {"value": "https://stlakedevx1.dfs.core.windows.net/"},
    "key_vault_uri": {"value": "https://kv-cvm-dev-x1.vault.azure.net/"},
    "funcao_host": {"value": "https://func-cvm-dev-x1.azurewebsites.net"},
    "resource_group_name": {"value": "rg-cvm-lake-dev"},
    "funcao_nome": {"value": "func-cvm-dev-x1"},
    "key_vault_nome": {"value": "kv-cvm-dev-x1"},
}


def test_mapa_e_os_parametros_do_deploy_e_aponta_para_saidas_que_existem() -> None:
    assert set(parametros_arm.MAPA) == PARAMETROS_DO_DEPLOY
    assert set(parametros_arm.MAPA.values()) <= SAIDAS_TF
    assert set(variaveis.NOMES) <= SAIDAS_TF


def test_troca_os_enderecos_e_mantem_o_resto() -> None:
    gerado = parametros_arm.gerar(SAIDAS, _parametros_exemplo())["parameters"]
    assert gerado["factoryName"] == {"value": "adf-cvm-lake-dev-x1"}
    assert gerado["ls_funcao_properties_typeProperties_functionAppUrl"]["value"] == (
        "https://func-cvm-dev-x1.azurewebsites.net"
    )
    assert (
        gerado["ls_http_cvm_properties_typeProperties_url"]["value"] == "https://dados.cvm.gov.br/"
    )
    assert gerado["tr_diario_properties_pl_inf_diario_parameters_de"]["value"] == 0
    assert ".invalid" not in json.dumps(gerado)


def test_endereco_novo_sem_saida_falha_antes_da_azure() -> None:
    parametros = _parametros_exemplo()
    parametros["parameters"]["ls_novo_properties_typeProperties_url"] = {
        "value": "https://novo.definido-no-deploy.invalid"
    }
    with pytest.raises(ValueError, match="ls_novo"):
        parametros_arm.gerar(SAIDAS, parametros)
    with pytest.raises(ValueError, match="funcao_host"):
        parametros_arm.gerar(
            {k: v for k, v in SAIDAS.items() if k != "funcao_host"}, _parametros_exemplo()
        )
    with pytest.raises(ValueError, match="vazia ou de exemplo"):
        parametros_arm.gerar(SAIDAS | {"adf_nome": {"value": ""}}, _parametros_exemplo())


@sem_arm
def test_parametros_do_arm_exportado_saem_sem_nenhum_invalid() -> None:
    assert ARM is not None
    parametros = json.loads((ARM / "ARMTemplateParametersForFactory.json").read_text())
    assert ".invalid" not in json.dumps(parametros_arm.gerar(SAIDAS, parametros))


DEPLOY_YML = (RAIZ / "ci/azure/deploy.yml").read_text(encoding="utf-8")
GATILHOS = sorted(p.stem for p in (RAIZ / "adf/trigger").glob("*.json"))


def _chamadas_do_script() -> list[str]:
    """Os ScriptArguments das duas chamadas ao PrePostDeploymentScript.ps1."""
    blocos = re.findall(
        r"ScriptPath: .*PrePostDeploymentScript\.ps1\n\s*ScriptArguments: >-\n"
        r"((?:\s{22}.*\n)+)",
        DEPLOY_YML,
    )
    return [" ".join(b.split()) for b in blocos]


def test_pre_script_para_todos_os_gatilhos_e_os_dois_leem_os_parametros_do_ambiente() -> None:
    pre, pos = _chamadas_do_script()
    assert "-predeployment $true" in pre and "-predeployment $false" in pos
    # Sem a lista, o script só para os gatilhos cujo conteúdo mudou.
    lista = re.search(r"-ExplicitStopTriggerList (\S+)", pre)
    assert lista and sorted(lista.group(1).split(",")) == GATILHOS
    # O export grava ARMTemplateParametersForFactory.json, e o padrão do script procura
    # ArmTemplateParametersForFactory.json: no Linux, só o caminho explícito funciona.
    for chamada in (pre, pos):
        assert '-ArmTemplateParameters "$(Agent.TempDirectory)/parametros.json"' in chamada


@sem_arm
def test_o_script_gerado_aceita_os_argumentos_que_o_deploy_passa() -> None:
    assert ARM is not None
    script = (ARM / "PrePostDeploymentScript.ps1").read_text(encoding="utf-8-sig")
    declarados = set(re.findall(r"\]\s*\[[\w\[\]]+\]\s*\$(\w+)", script))
    for chamada in _chamadas_do_script():
        for argumento in re.findall(r"(?:^| )-(\w+)", chamada):
            assert argumento.lower() in {d.lower() for d in declarados}, argumento


def test_variaveis_do_job_e_recusa_valor_que_injeta_comando() -> None:
    assert variaveis.linhas(SAIDAS) == [
        "##vso[task.setvariable variable=resource_group_name]rg-cvm-lake-dev",
        "##vso[task.setvariable variable=adf_nome]adf-cvm-lake-dev-x1",
        "##vso[task.setvariable variable=funcao_nome]func-cvm-dev-x1",
        "##vso[task.setvariable variable=key_vault_nome]kv-cvm-dev-x1",
    ]
    with pytest.raises(ValueError):
        variaveis.linhas(SAIDAS | {"adf_nome": {"value": "x\n##vso[task.setvariable variable=y]z"}})


# --- ci/deploy/funcao.sh com um az falso --------------------------------------------------------

AZ_FALSO = r"""#!/usr/bin/env bash
# az falso: registra os argumentos e guarda chave e segredo em $FALSO.
printf '%s\n' "$*" >>"$FALSO/chamadas"
case "$1 $2 $3" in
  "functionapp deployment source") exit 0 ;;
  "functionapp keys list") cat "$FALSO/chave" 2>/dev/null; exit 0 ;;
  "functionapp keys set") printf 'S3GR3D0-da-funcao' >"$FALSO/chave"; exit 0 ;;
  "keyvault secret show") [[ -f "$FALSO/segredo" ]] || { echo "SecretNotFound" >&2; exit 3; }
                          cat "$FALSO/segredo"; exit 0 ;;
  "keyvault secret set")
    while [[ $# -gt 0 ]]; do [[ "$1" == "--file" ]] && cp "$2" "$FALSO/segredo"; shift; done
    exit 0 ;;
esac
echo "chamada inesperada: $*" >&2; exit 9
"""


def _rodar_funcao_sh(falso: Path) -> subprocess.CompletedProcess[str]:
    bin_ = falso / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "az").write_text(AZ_FALSO)
    (bin_ / "az").chmod(0o755)
    env = os.environ | {
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "FALSO": str(falso),
        "ESPERA_RBAC_S": "0",
    }
    return subprocess.run(
        [
            "bash",
            str(RAIZ / "ci/deploy/funcao.sh"),
            "rg-cvm-lake-dev",
            "func-x",
            "kv-x",
            "/tmp/funcao.zip",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def test_funcao_sh_publica_cria_a_chave_e_grava_o_segredo_sem_expor(tmp_path: Path) -> None:
    primeira = _rodar_funcao_sh(tmp_path)
    chamadas = (tmp_path / "chamadas").read_text()
    assert "--build-remote true" in chamadas and "--src /tmp/funcao.zip" in chamadas
    assert (
        "keys set --resource-group rg-cvm-lake-dev --name func-x --key-type functionKeys"
        " --key-name adf" in chamadas
    )
    assert "--query functionKeys.adf" in chamadas
    assert (tmp_path / "segredo").read_text() == "S3GR3D0-da-funcao"
    # O valor nunca vai na linha de comando; na saída, só na linha que o mascara no pipeline.
    assert "S3GR3D0" not in chamadas
    linhas_com_valor = [
        x for x in (primeira.stdout + primeira.stderr).splitlines() if "S3GR3D0" in x
    ]
    assert linhas_com_valor == ["##vso[task.setsecret]S3GR3D0-da-funcao"]
    assert "criada" in primeira.stdout and "gravado" in primeira.stdout


def test_funcao_sh_segunda_vez_nao_recria_a_chave_nem_regrava(tmp_path: Path) -> None:
    _rodar_funcao_sh(tmp_path)
    (tmp_path / "chamadas").unlink()
    segunda = _rodar_funcao_sh(tmp_path)
    chamadas = (tmp_path / "chamadas").read_text()
    assert "keys set" not in chamadas and "secret set" not in chamadas
    assert "já existe" in segunda.stdout and "nenhuma versão nova" in segunda.stdout


# --- ci/deploy/terraform.sh com terraform e az falsos -------------------------------------------

TF_FALSO = r"""#!/usr/bin/env bash
printf '%s | OIDC=%s SC=%s CLIENT=%s SUB=%s\n' "$*" "${ARM_USE_OIDC:-}" \
  "${ARM_ADO_PIPELINE_SERVICE_CONNECTION_ID:-}" "${ARM_CLIENT_ID:-}" "${ARM_SUBSCRIPTION_ID:-}" \
  >>"$FALSO/chamadas"
case "$1" in
  output) echo '{"adf_nome": {"value": "adf-x"}}' ;;
  plan) for a in "$@"; do [[ "$a" == -out=* ]] && touch "${a#-out=}"; done ;;
esac
"""


@pytest.fixture
def ambiente_tf(tmp_path: Path) -> dict[str, str]:
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "terraform").write_text(TF_FALSO)
    (bin_ / "az").write_text("#!/usr/bin/env bash\necho 00000000-sub\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    return {
        "PATH": f"{bin_}:/usr/bin:/bin",
        "FALSO": str(tmp_path),
        "HOME": str(tmp_path),
        "TF_ESTADO_RG": "rg-estado",
        "TF_ESTADO_CONTA": "stestado",
        "TF_ESTADO_CONTAINER": "tfstate",
        "servicePrincipalId": "cliente-sc",
        "tenantId": "tenant-sc",
        "AZURESUBSCRIPTION_SERVICE_CONNECTION_ID": "id-da-sc",
        "SYSTEM_ACCESSTOKEN": "token",
        "SYSTEM_OIDCREQUESTURI": "https://oidc",
        "TF_VAR_emails_alerta": '["a@example.com"]',
    }


def _tf(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(RAIZ / "ci/deploy/terraform.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_terraform_sh_planeja_com_o_estado_do_ambiente_e_oidc(
    tmp_path: Path, ambiente_tf: dict[str, str]
) -> None:
    r = _tf(["plano", "prod", str(tmp_path / "p" / "prod.tfplan")], ambiente_tf)
    assert r.returncode == 0, r.stderr
    init, plan = (tmp_path / "chamadas").read_text().splitlines()
    assert "-backend-config=key=prod.tfstate" in init
    assert "-backend-config=storage_account_name=stestado" in init
    assert "-var-file=ambientes/prod.tfvars" in plan and "-out=" in plan
    assert "OIDC=true SC=id-da-sc CLIENT=cliente-sc SUB=00000000-sub" in plan
    assert (tmp_path / "p" / "prod.tfplan").exists()


def test_terraform_sh_aplica_o_plano_salvo_e_grava_as_saidas(
    tmp_path: Path, ambiente_tf: dict[str, str]
) -> None:
    plano = tmp_path / "dev.tfplan"
    r = _tf(["aplicar", "dev", str(plano), str(tmp_path / "s" / "saidas.json")], ambiente_tf)
    assert r.returncode == 0, r.stderr
    chamadas = (tmp_path / "chamadas").read_text().splitlines()
    assert chamadas[1].startswith(f"apply -no-color {plano} |")  # sem -var-file: o plano já traz
    assert json.loads((tmp_path / "s" / "saidas.json").read_text()) == {
        "adf_nome": {"value": "adf-x"}
    }


@pytest.mark.parametrize(
    "args", [[], ["plano", "hml", "x"], ["aplicar", "dev", "x"], ["destruir", "dev", "x"]]
)
def test_terraform_sh_recusa_acao_ou_ambiente_fora_da_lista(
    args: list[str], ambiente_tf: dict[str, str]
) -> None:
    assert _tf(args, ambiente_tf).returncode == 2


def test_terraform_sh_sem_token_do_pipeline_nao_chega_ao_terraform(
    tmp_path: Path, ambiente_tf: dict[str, str]
) -> None:
    env = {k: v for k, v in ambiente_tf.items() if k != "SYSTEM_ACCESSTOKEN"}
    r = _tf(["plano", "dev", str(tmp_path / "dev.tfplan")], env)
    assert r.returncode != 0 and "System.AccessToken" in r.stderr
    assert not (tmp_path / "chamadas").exists()
