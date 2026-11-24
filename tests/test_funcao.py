"""A Function na Azure: o adaptador do ADLS, o contrato HTTP e as rotas do runtime.

O ADLS é simulado por um serviço falso sobre uma pasta, com a mesma interface do SDK
(``get_file_client``, ``get_file_system_client().get_paths``). A prova roda de ponta a ponta
por esse adaptador, com o DuckDB real lendo o que foi baixado.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import azure.functions as func
import function_app  # funcao_azure/, no pythonpath do pytest e no mypy_path
import pytest
from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError
from azure.functions.decorators.http import HttpTrigger

from cvm_lake import adf_local
from cvm_lake.funcao import entrada
from cvm_lake.funcao.adls import ArmazenamentoADLS
from cvm_lake.funcao.armazenamento import ArmazenamentoLocal
from cvm_lake.lake import Lake
from cvm_lake.origem import OrigemPasta

RAIZ = Path(__file__).resolve().parents[1]
CONTA = "https://stcvmlakedev.dfs.core.windows.net"
CARGA = "5b0c7a3e-1d2f-4e6a-9c8b-7a6f5e4d3c2b"

# --- ADLS falso: pasta local atrás da interface do SDK -----------------------------------------


class _Download:
    def __init__(self, conteudo: bytes) -> None:
        self._conteudo = conteudo

    def readall(self) -> bytes:
        return self._conteudo


@dataclass
class _Propriedades:
    size: int


@dataclass
class _Item:
    name: str
    is_directory: bool


class _Arquivo:
    def __init__(self, caminho: Path, falha: Exception | None, conta: Path) -> None:
        self._caminho, self._falha, self._conta = caminho, falha, conta

    def _checar(self) -> None:
        if self._falha:
            raise self._falha
        if not self._caminho.is_file():
            raise ResourceNotFoundError("The specified path does not exist.")

    def download_file(self) -> _Download:
        self._checar()
        return _Download(self._caminho.read_bytes())

    def upload_data(self, conteudo: bytes, overwrite: bool) -> None:
        assert overwrite is True
        if self._falha:
            raise self._falha
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        self._caminho.write_bytes(conteudo)

    def rename_file(self, novo: str) -> _Arquivo:
        # O SDK recebe "<container>/<caminho>" e sobrescreve o destino.
        self._checar()
        destino = self._conta / novo
        destino.parent.mkdir(parents=True, exist_ok=True)
        self._caminho.replace(destino)
        return _Arquivo(destino, self._falha, self._conta)

    def exists(self) -> bool:
        return self._caminho.is_file()

    def get_file_properties(self) -> _Propriedades:
        self._checar()
        return _Propriedades(self._caminho.stat().st_size)


class _Container:
    def __init__(self, pasta: Path, falha: Exception | None) -> None:
        self._pasta, self._falha = pasta, falha

    def get_paths(self, path: str, recursive: bool) -> Iterator[_Item]:
        assert recursive is False
        alvo = self._pasta / path
        if not alvo.is_dir():
            raise ResourceNotFoundError("The specified path does not exist.")
        for filho in sorted(alvo.iterdir()):
            yield _Item(f"{path}/{filho.name}", filho.is_dir())

    def get_file_client(self, nome: str) -> _Arquivo:
        return _Arquivo(self._pasta / nome, self._falha, self._pasta.parent)


class ServicoFalso:
    def __init__(self, pasta: Path, falha: Exception | None = None) -> None:
        self.url = CONTA + "/"  # o SDK devolve a URL da conta com a barra final
        self.pasta, self.falha = pasta, falha

    def get_file_client(self, container: str, caminho: str) -> _Arquivo:
        return _Arquivo(self.pasta / container / caminho, self.falha, self.pasta)

    def get_file_system_client(self, container: str) -> _Container:
        return _Container(self.pasta / container, self.falha)


def adls(pasta: Path, falha: Exception | None = None) -> ArmazenamentoADLS:
    return ArmazenamentoADLS(cast(Any, ServicoFalso(pasta, falha)))


# --- o adaptador --------------------------------------------------------------------------------


def test_raiz_e_o_endpoint_dfs_e_o_primeiro_segmento_e_o_container(tmp_path: Path) -> None:
    arm = adls(tmp_path)
    lake = Lake(arm.raiz)
    assert arm.raiz == CONTA
    assert arm.dividir(lake.prata("202610")) == ("prata", "cvm/inf_diario/competencia=202610")
    for fora in ("https://outra.dfs.core.windows.net/prata/x", f"{CONTA}/prata", f"{CONTA}/"):
        with pytest.raises(ValueError, match="fora do lake"):
            arm.dividir(fora)


def test_le_grava_confere_e_mede_pelo_sdk(tmp_path: Path) -> None:
    with adls(tmp_path) as arm:
        caminho = f"{CONTA}/controle/cvm/inf_diario/competencia=202610/manifesto.json"
        assert arm.existe(caminho) is False
        arm.gravar_bytes(caminho, b'{"a": 1}')
        assert arm.ler_bytes(caminho) == b'{"a": 1}'
        assert arm.existe(caminho) is True and arm.tamanho(caminho) == 8
        assert (tmp_path / "controle/cvm/inf_diario/competencia=202610/manifesto.json").exists()


def test_regravar_troca_o_arquivo_inteiro_sem_deixar_temporario(tmp_path: Path) -> None:
    with adls(tmp_path) as arm:
        caminho = f"{CONTA}/ouro/cvm/inf_diario_mensal/competencia=202610/ouro.parquet"
        arm.gravar_bytes(caminho, b"versao 1")
        arm.gravar_bytes(caminho, b"versao 2, maior")
        assert arm.ler_bytes(caminho) == b"versao 2, maior"
        pasta = tmp_path / "ouro/cvm/inf_diario_mensal/competencia=202610"
        assert [p.name for p in pasta.iterdir()] == ["ouro.parquet"]


def test_falha_no_upload_deixa_a_versao_anterior(tmp_path: Path) -> None:
    caminho = f"{CONTA}/controle/cvm/inf_diario/competencia=202610/manifesto.json"
    with adls(tmp_path) as arm:
        arm.gravar_bytes(caminho, b'{"versao": 1}')
    with adls(tmp_path, falha=ClientAuthenticationError("token vencido")) as arm, \
            pytest.raises(OSError):
        arm.gravar_bytes(caminho, b'{"versao": 2}')
    assert (tmp_path / "controle/cvm/inf_diario/competencia=202610/manifesto.json"
            ).read_bytes() == b'{"versao": 1}'


def test_baixa_so_os_parquet_da_pasta_e_apaga_ao_fechar(tmp_path: Path) -> None:
    pasta = tmp_path / "prata/cvm/inf_diario/competencia=202610"
    (pasta / "sub").mkdir(parents=True)
    (pasta / "inf_diario_fi_202610.parquet").write_bytes(b"PAR1")
    (pasta / "_SUCCESS").write_bytes(b"")
    (pasta / "sub" / "outro.parquet").write_bytes(b"PAR1")

    arm = adls(tmp_path)
    glob = arm.caminho_duckdb(f"{CONTA}/prata/cvm/inf_diario/competencia=202610/*.parquet")
    local = Path(glob.removesuffix("/*.parquet"))
    assert [p.name for p in local.iterdir()] == ["inf_diario_fi_202610.parquet"]
    arm.fechar()
    assert not local.exists()


def test_erro_do_servico_vira_oserror_com_o_caminho(tmp_path: Path) -> None:
    arm = adls(tmp_path)
    with pytest.raises(OSError, match="bronze/x.zip: ResourceNotFoundError"):
        arm.ler_bytes(f"{CONTA}/bronze/x.zip")
    with pytest.raises(OSError, match="ResourceNotFoundError"):
        arm.caminho_duckdb(f"{CONTA}/staging/nao/existe/*.parquet")
    negado = adls(tmp_path, ClientAuthenticationError("identidade sem papel"))
    with pytest.raises(OSError, match="ClientAuthenticationError"):
        negado.gravar_bytes(f"{CONTA}/ouro/x.parquet", b"")


def test_so_aceita_a_pasta_inteira_de_parquet(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"\*\.parquet"):
        adls(tmp_path).caminho_duckdb(f"{CONTA}/prata/cvm/arquivo.parquet")


# --- a prova de ponta a ponta pelo adaptador ----------------------------------------------------


def _carga_ate_a_staging(snapshot: Path, pasta_lake: Path) -> None:
    """Passos 1 e 2 do filho pelo substituto local, no layout que o adaptador lê."""
    lake_local = Lake(str(pasta_lake))
    adf_local.copy_origem_para_bronze(OrigemPasta(snapshot), lake_local, "202610", CARGA)
    adf_local.copy_zip_para_staging(lake_local, "202610", CARGA)


def _cabecalhos(bytes_servidor: int | None) -> Callable[[str], dict[str, Any]]:
    def cabecalhos(competencia: str) -> dict[str, Any]:
        return {"url": f"https://cvm/{competencia}", "etag": '"v1"', "last_modified": "x",
                "bytes": bytes_servidor, "obtido_em": "agora"}
    return cabecalhos


def _corpo(**campos: Any) -> bytes:
    return json.dumps({"competencia": "202610", "carga": CARGA, **campos}).encode()


def test_validar_e_reconciliar_pelo_adls(snapshot: Path, tmp_path: Path) -> None:
    pasta = tmp_path / "lake"
    _carga_ate_a_staging(snapshot, pasta)
    with adls(pasta) as arm:
        lake = Lake(arm.raiz)
        status, validacao = entrada.tratar("validar", _corpo(), arm, lake, _cabecalhos(None))
        assert (status, validacao["ok"]) == (200, True), validacao["falhas"]

        # A troca da partição (passos 4 e 5) é do ADF; aqui, o substituto local.
        local = Lake(str(pasta))
        adf_local.delete(local.prata("202610"))
        adf_local.copy_pasta(local.staging("202610", CARGA), local.prata("202610"))
        tamanho = arm.tamanho(lake.bronze_zip("202610", CARGA))
        status, rec = entrada.tratar("reconciliar", _corpo(), arm, lake, _cabecalhos(tamanho))
        assert (status, rec["ok"]) == (200, True), rec["falhas"]
        manifesto = json.loads(arm.ler_bytes(lake.manifesto("202610")))
    assert manifesto["carga_vigente"] == CARGA
    assert manifesto["origem"]["etag"] == '"v1"'
    assert manifesto["origem"]["confere_com_bronze"] is True
    assert list((pasta / "ouro/cvm/inf_diario_mensal/competencia=202610").glob("*.parquet"))


def test_staging_divergente_responde_200_com_ok_false(snapshot: Path, tmp_path: Path) -> None:
    pasta = tmp_path / "lake"
    _carga_ate_a_staging(snapshot, pasta)
    parquet = pasta / "staging/cvm/inf_diario" / f"carga={CARGA}" / "competencia=202610"
    for arquivo in parquet.iterdir():
        arquivo.unlink()
    with adls(pasta) as arm:
        status, resposta = entrada.tratar("validar", _corpo(), arm, Lake(arm.raiz),
                                          _cabecalhos(None))
    assert status == 200 and resposta["ok"] is False
    assert any("No files found" in f for f in resposta["falhas"])


def test_etag_de_outra_versao_nao_passa_por_conferido(snapshot: Path, tmp_path: Path) -> None:
    """A CVM regravou o mês entre o Copy e o HEAD: o tamanho servido não é o da bronze."""
    pasta = tmp_path / "lake"
    _carga_ate_a_staging(snapshot, pasta)
    arm, lake = ArmazenamentoLocal(), Lake(str(pasta))
    meta = entrada.metadados_da_origem(_cabecalhos(1), arm, lake, "202610", CARGA)
    assert meta["confere_com_bronze"] is False and meta["bytes_bronze"] > 1


def test_head_que_falha_nao_derruba_a_carga(snapshot: Path, tmp_path: Path) -> None:
    def fora_do_ar(competencia: str) -> dict[str, Any]:
        raise TimeoutError("timed out")

    arm, lake = ArmazenamentoLocal(), Lake(str(tmp_path / "lake"))
    assert entrada.metadados_da_origem(fora_do_ar, arm, lake, "202610", CARGA) == {
        "erro": "TimeoutError: timed out"}


@pytest.mark.parametrize("corpo", [
    b"nao e json", b"[]", b'{"carga": "x"}', b'{"competencia": "202613", "carga": "x"}',
    b'{"competencia": 202610, "carga": "x"}', b'{"competencia": "202610", "carga": "../x"}',
])
def test_corpo_invalido_responde_400_sem_tocar_no_lake(corpo: bytes, tmp_path: Path) -> None:
    lake = Lake(str(tmp_path / "lake"))
    status, resposta = entrada.tratar("validar", corpo, ArmazenamentoLocal(), lake,
                                      _cabecalhos(None))
    assert status == 400 and resposta["ok"] is False
    assert resposta["falhas"][0].startswith("pedido inválido")
    assert not (tmp_path / "lake").exists()


# --- o runtime da Azure ------------------------------------------------------------------------


def test_rotas_sao_os_function_name_do_adf_por_post_com_chave() -> None:
    filho = json.loads((RAIZ / "adf/pipeline/pl_inf_diario_mes.json").read_text())
    nomes_no_adf = {a["typeProperties"]["functionName"]
                    for a in filho["properties"]["activities"]
                    if a["type"] == "AzureFunctionActivity"}
    rotas = {}
    for funcao in function_app.app.get_functions():
        gatilho = next(b for b in funcao.get_bindings() if isinstance(b, HttpTrigger))
        rotas[gatilho.route] = (gatilho.methods, gatilho.auth_level)
    assert set(rotas) == nomes_no_adf == set(entrada.ETAPAS)
    for metodos, nivel in rotas.values():
        assert [m.value for m in metodos or []] == ["POST"]
        assert nivel == func.AuthLevel.FUNCTION


def test_resposta_http_e_o_json_da_prova(snapshot: Path, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    pasta = tmp_path / "lake"
    _carga_ate_a_staging(snapshot, pasta)
    monkeypatch.setenv("LAKE_CONTA", "stcvmlakedev")
    monkeypatch.setattr(ArmazenamentoADLS, "da_conta", classmethod(lambda cls, conta: adls(pasta)))
    pedido = func.HttpRequest("POST", "/api/validar", body=_corpo())
    resposta = function_app.responder("validar", pedido)
    assert resposta.status_code == 200
    assert resposta.mimetype == "application/json"
    corpo = json.loads(resposta.get_body())
    assert corpo["etapa"] == "validar" and corpo["ok"] is True


class _RecusaGravar(ArmazenamentoADLS):
    """ADLS que recusa gravar os caminhos que contêm ``trecho`` (papel faltando, rede caída)."""

    def __init__(self, pasta: Path, trecho: str) -> None:
        super().__init__(cast(Any, ServicoFalso(pasta)))
        self._trecho = trecho

    def gravar_bytes(self, caminho: str, conteudo: bytes) -> None:
        if self._trecho in caminho:
            raise OSError(f"{caminho}: ClientAuthenticationError: sem papel")
        super().gravar_bytes(caminho, conteudo)


def test_controle_nao_gravado_vira_ok_false_e_nao_derruba_o_json(
        snapshot: Path, tmp_path: Path) -> None:
    pasta = tmp_path / "lake"
    _carga_ate_a_staging(snapshot, pasta)
    with _RecusaGravar(pasta, "/controle/") as arm:
        status, validacao = entrada.tratar("validar", _corpo(), arm, Lake(arm.raiz),
                                           _cabecalhos(None))
    assert (status, validacao["ok"]) == (200, False)
    assert any("controle não gravado" in f for f in validacao["falhas"])


def test_manifesto_nao_gravado_deixa_o_anterior_e_o_relatorio_diz_por_que(
        snapshot: Path, tmp_path: Path) -> None:
    pasta = tmp_path / "lake"
    _carga_ate_a_staging(snapshot, pasta)
    local = Lake(str(pasta))
    adf_local.delete(local.prata("202610"))
    adf_local.copy_pasta(local.staging("202610", CARGA), local.prata("202610"))
    with _RecusaGravar(pasta, "manifesto.json") as arm:
        lake = Lake(arm.raiz)
        status, rec = entrada.tratar("reconciliar", _corpo(), arm, lake, _cabecalhos(None))
        relatorio = json.loads(arm.ler_bytes(lake.relatorio_carga("202610", CARGA)))
        assert arm.existe(lake.manifesto("202610")) is False
    assert (status, rec["ok"]) == (200, False)
    assert relatorio["reconciliacao"]["ok"] is False
    assert any("manifesto.json" in f for f in relatorio["reconciliacao"]["falhas"])
