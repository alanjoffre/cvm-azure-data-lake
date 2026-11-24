"""O JSON do ADF (``adf/``) contra o código que a prova local já testou.

O validador da Microsoft confere esquema, referências entre recursos e parâmetros, mas aceita um
``dependsOn`` que aponta para uma atividade inexistente (conferido trocando o nome: "No errors
found"). Estes testes cobrem o que ele não cobre: a ordem dos passos, os caminhos e as
competências calculados pelas expressões (``adf_expressao.py``) iguais aos de ``lake.py`` e
``janela.py``, o corpo da Function igual aos parâmetros de ``prova.py``, os tipos do Copy aceitos
pela validação e as regras de segurança.

Os testes do ARM rodam quando ``ADF_ARM`` aponta para a saída do export (``ci/adf/validar.sh``).
"""

from __future__ import annotations

import fnmatch
import inspect
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pytest
from adf_expressao import FUSOS_WINDOWS, Contexto, ErroDeExpressao, avaliar, funcoes_usadas
from conftest import linhas_csv, montar_csv, montar_zip

from cvm_lake import esquema
from cvm_lake.funcao import prova
from cvm_lake.funcao.armazenamento import ArmazenamentoLocal
from cvm_lake.janela import JANELA_DIARIA, JANELA_SEMANAL, janela
from cvm_lake.lake import Lake, parquets
from cvm_lake.origem import URL_BASE_CVM, nome_arquivo

RAIZ = Path(__file__).resolve().parents[1]
ADF = RAIZ / "adf"
BRASILIA = ZoneInfo("America/Sao_Paulo")
CARGA = "5b0c7a3e-1d2f-4e6a-9c8b-7a6f5e4d3c2b"  # formato do RunId do ADF


def _recursos(tipo: str) -> dict[str, dict[str, Any]]:
    saida: dict[str, dict[str, Any]] = {}
    for arquivo in sorted((ADF / tipo).glob("*.json")):
        dado = json.loads(arquivo.read_text(encoding="utf-8"))
        assert dado["name"] == arquivo.stem, f"{arquivo.name}: nome diferente do arquivo"
        saida[dado["name"]] = dado.get("properties", dado)
    return saida


LS = _recursos("linkedService")
DS = _recursos("dataset")
PL = _recursos("pipeline")
TR = _recursos("trigger")
PAI = PL["pl_inf_diario"]
FILHO = PL["pl_inf_diario_mes"]


def _atividades(lista: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Todas as atividades, inclusive as de dentro de IfCondition e ForEach."""
    for a in lista:
        yield a
        tp = a.get("typeProperties", {})
        for chave in ("ifTrueActivities", "ifFalseActivities", "activities"):
            yield from _atividades(tp.get(chave, []))


def _filho(nome: str) -> dict[str, Any]:
    return next(a for a in _atividades(FILHO["activities"]) if a["name"] == nome)


def _referencias(no: Any) -> Iterator[dict[str, Any]]:
    if isinstance(no, dict):
        if "referenceName" in no:
            yield no
        for v in no.values():
            yield from _referencias(v)
    elif isinstance(no, list):
        for v in no:
            yield from _referencias(v)


def _expressoes(no: Any) -> Iterator[str]:
    if isinstance(no, dict):
        if no.get("type") == "Expression":
            yield no["value"]
        for v in no.values():
            yield from _expressoes(v)
    elif isinstance(no, list):
        for v in no:
            yield from _expressoes(v)
    elif isinstance(no, str) and no.startswith("@"):
        yield no


# --- referências ---------------------------------------------------------------------------

def test_toda_referencia_aponta_para_um_recurso_que_existe() -> None:
    existentes = {"LinkedServiceReference": LS, "DatasetReference": DS, "PipelineReference": PL}
    todos = [*LS.values(), *DS.values(), *PL.values(), *TR.values()]
    refs = [r for recurso in todos for r in _referencias(recurso)]
    assert refs
    for r in refs:
        assert r["referenceName"] in existentes[r["type"]], r


def test_parametros_passados_a_dataset_e_a_pipeline_sao_os_declarados() -> None:
    conferidos = 0
    for pipeline in PL.values():
        for a in _atividades(pipeline["activities"]):
            tp = a.get("typeProperties", {})
            refs = [*a.get("inputs", []), *a.get("outputs", [])]
            refs += [tp["dataset"]] if "dataset" in tp else []
            for r in refs:
                declarados = DS[r["referenceName"]].get("parameters", {})
                assert set(r.get("parameters", {})) == set(declarados), (a["name"], r)
                conferidos += 1
            if a["type"] == "ExecutePipeline":
                declarados = PL[tp["pipeline"]["referenceName"]]["parameters"]
                assert set(tp["parameters"]) == set(declarados), a["name"]
                conferidos += 1
    assert conferidos == 10  # 9 datasets no filho + o Execute Pipeline do pai


def test_expressoes_so_usam_parametros_declarados_e_funcoes_cobertas() -> None:
    for nome, recurso in [*PL.items(), *DS.items()]:
        declarados = set(recurso.get("parameters", {}))
        for texto in _expressoes(recurso):
            funcoes_usadas(texto)  # lê a expressão inteira; função desconhecida falha ao avaliar
            for usado in re.findall(r"(?:pipeline\(\)\.parameters|dataset\(\))\.(\w+)", texto):
                assert usado in declarados, (nome, texto)


def test_depends_on_aponta_para_atividade_irma_e_so_em_sucesso() -> None:
    def conferir(lista: list[dict[str, Any]]) -> None:
        nomes = {a["name"] for a in lista}
        for a in lista:
            for d in a["dependsOn"]:
                assert d["activity"] in nomes, (a["name"], d)
                assert d["dependencyConditions"] == ["Succeeded"], (a["name"], d)
            tp = a.get("typeProperties", {})
            for chave in ("ifTrueActivities", "ifFalseActivities", "activities"):
                conferir(tp.get(chave, []))

    for pipeline in PL.values():
        conferir(pipeline["activities"])


# --- o pipeline filho: ordem, caminhos e Function -------------------------------------------

ORDEM = [
    "copiar_origem_para_bronze",   # 1
    "copiar_zip_para_staging",     # 2
    "validar_staging",             # 3
    "validacao_ok",
    "prata_existe",
    "apagar_prata_se_existe",      # 4
    "copiar_staging_para_prata",   # 5
    "reconciliar_prata",           # 6
    "reconciliacao_ok",
    "apagar_staging",              # 7
]


def test_filho_e_uma_cadeia_na_ordem_dos_sete_passos() -> None:
    atividades = FILHO["activities"]
    assert [a["name"] for a in atividades] == ORDEM
    assert atividades[0]["dependsOn"] == []
    for anterior, atual in zip(atividades, atividades[1:], strict=False):
        assert [d["activity"] for d in atual["dependsOn"]] == [anterior["name"]]


def _caminho(ref: dict[str, Any], ctx: Contexto) -> str:
    p = {k: avaliar(v, ctx) for k, v in ref["parameters"].items()}
    partes = ["lake", p["container"], p["pasta"]] + ([p["arquivo"]] if "arquivo" in p else [])
    return "/".join(partes)


@pytest.mark.parametrize("competencia", ["202601", "202611", "202612"])
def test_caminhos_do_filho_sao_os_de_lake_py(competencia: str) -> None:
    ctx = Contexto(parametros={"competencia": competencia, "carga": CARGA})
    lake = Lake("lake")

    def io(nome: str, lado: str) -> str:
        (ref,) = _filho(nome)[lado]
        return _caminho(ref, ctx)

    def ds(nome: str) -> str:
        return _caminho(_filho(nome)["typeProperties"]["dataset"], ctx)

    bronze = lake.bronze_zip(competencia, CARGA)
    staging = lake.staging(competencia, CARGA)
    prata = lake.prata(competencia)
    assert io("copiar_origem_para_bronze", "outputs") == bronze
    assert io("copiar_zip_para_staging", "inputs") == bronze
    parquet = f"{staging}/inf_diario_fi_{competencia}.parquet"
    assert io("copiar_zip_para_staging", "outputs") == parquet
    assert fnmatch.fnmatch(io("copiar_zip_para_staging", "outputs"), parquets(staging))
    assert ds("prata_existe") == prata
    assert ds("apagar_prata") == prata
    assert io("copiar_staging_para_prata", "inputs") == staging
    assert io("copiar_staging_para_prata", "outputs") == prata
    assert ds("apagar_staging") == staging


@pytest.mark.parametrize("competencia", ["202601", "202611"])
def test_url_da_cvm_e_a_de_origem_py(competencia: str) -> None:
    (ref,) = _filho("copiar_origem_para_bronze")["inputs"]
    dataset = DS[ref["referenceName"]]
    ls = LS[dataset["linkedServiceName"]["referenceName"]]
    params = {k: avaliar(v, Contexto(parametros={"competencia": competencia}))
              for k, v in ref["parameters"].items()}
    relativa = avaliar(dataset["typeProperties"]["location"]["relativeUrl"],
                       Contexto(dataset=params))
    assert ls["typeProperties"]["url"] + relativa == f"{URL_BASE_CVM}/{nome_arquivo(competencia)}"


FUNCOES = ["validar_staging", "reconciliar_prata"]


@pytest.mark.parametrize("nome", FUNCOES)
def test_corpo_da_function_e_o_que_prova_py_recebe(nome: str) -> None:
    atividade = _filho(nome)
    tp = atividade["typeProperties"]
    etapa = getattr(prova, tp["functionName"])
    obrigatorios = {n for n, p in inspect.signature(etapa).parameters.items()
                    if p.default is inspect.Parameter.empty} - {"arm", "lake"}
    corpo = avaliar(tp["body"], Contexto(parametros={"competencia": "202611", "carga": CARGA}))
    assert corpo == {"competencia": "202611", "carga": CARGA}
    assert set(corpo) == obrigatorios
    assert tp["method"] == "POST"
    assert atividade["policy"]["retry"] == 0  # resposta determinística: repetir não muda nada


@pytest.mark.parametrize("nome", FUNCOES)
def test_resposta_com_ok_false_cai_numa_atividade_fail(nome: str) -> None:
    (guarda,) = [a for a in FILHO["activities"]
                 if [d["activity"] for d in a["dependsOn"]] == [nome]]
    assert guarda["type"] == "IfCondition"
    tp = guarda["typeProperties"]
    assert tp.get("ifTrueActivities", []) == []
    (falha,) = tp["ifFalseActivities"]
    assert falha["type"] == "Fail"
    for resposta, esperado in (({"ok": True, "falhas": []}, True),
                               ({"ok": False, "falhas": ["a", "b"]}, False)):
        ctx = Contexto(atividades={nome: resposta})
        assert avaliar(tp["expression"], ctx) is esperado
    recusa = {"ok": False, "falhas": ["contagem 3 × 2", "x"]}
    mensagem = avaliar(falha["typeProperties"]["message"], Contexto(atividades={nome: recusa}))
    assert mensagem == "contagem 3 × 2; x"


def test_prata_so_e_apagada_se_existe() -> None:
    se = _filho("apagar_prata_se_existe")["typeProperties"]
    for existe in (True, False):
        ctx = Contexto(atividades={"prata_existe": {"exists": existe}})
        assert avaliar(se["expression"], ctx) is existe
    assert [a["name"] for a in se["ifTrueActivities"]] == ["apagar_prata"]
    assert _filho("prata_existe")["typeProperties"]["fieldList"] == ["exists"]


def test_retentativa_so_onde_repetir_e_seguro() -> None:
    for a in _atividades(FILHO["activities"]):
        if a["type"] in ("Copy", "Delete", "GetMetadata"):
            assert a["policy"]["retry"] == 2, a["name"]
        elif a["type"] == "AzureFunctionActivity":
            assert a["policy"]["retry"] == 0, a["name"]


# --- Copy ZIP → Parquet ---------------------------------------------------------------------

COPY_PARQUET = _filho("copiar_zip_para_staging")["typeProperties"]
# Tipos interinos do Copy → o que o Parquet guardaria, para simular a staging no DuckDB.
# A precisão do Decimal no Parquet não está documentada (D2b): aqui, a mais larga.
INTERINO_DUCKDB = {"String": "VARCHAR", "Int64": "BIGINT", "Decimal": "DECIMAL(38,18)"}


def test_copy_mapeia_as_colunas_do_dicionario_na_ordem() -> None:
    tradutor = COPY_PARQUET["translator"]
    nomes = [(m["source"]["name"], m["sink"]["name"]) for m in tradutor["mappings"]]
    assert nomes == [(n, n) for n in esquema.NOMES]
    assert tradutor["typeConversion"] is True
    assert tradutor["typeConversionSettings"]["allowDataTruncation"] is False


def test_copy_le_o_csv_de_dentro_do_zip_no_formato_da_cvm() -> None:
    leitura = COPY_PARQUET["source"]["formatSettings"]["compressionProperties"]
    assert leitura == {"type": "ZipDeflateReadSettings", "preserveZipFileNameAsFolder": False}
    (ref,) = _filho("copiar_zip_para_staging")["inputs"]
    csv = DS[ref["referenceName"]]["typeProperties"]
    assert csv["columnDelimiter"] == esquema.CSV_DELIMITADOR
    assert csv["compressionCodec"] == "ZipDeflate"
    assert csv["firstRowAsHeader"] is True
    # ISO-8859-1 contém o ASCII do arquivo (esquema.CSV_ENCODING): ler como latin-1 não muda byte.
    assert esquema.CSV_ENCODING == "ascii" and csv["encodingName"] == "ISO-8859-1"


def test_staging_com_os_tipos_do_copy_passa_na_validacao(tmp_path: Path) -> None:
    """A staging gravada com os tipos que o JSON pede ao Copy passa no prova.validar real.

    Data como texto ISO, NR_COTST como inteiro de 64 bits e decimais largos: a validação tipa
    pelo dicionário e o hash bate. Se o Copy gravar outra precisão, a reconciliação acusa (D2b).
    """
    lake = Lake(str(tmp_path / "lake"))
    arm = ArmazenamentoLocal()
    linhas = [list(linha) for linha in linhas_csv("202610")]
    linhas[0][esquema.NOMES.index("VL_QUOTA")] = "112439113.205550566316"
    linhas[0][esquema.NOMES.index("NR_COTST")] = "4294967296"  # não cabe em 32 bits
    csv = montar_csv(linhas)
    arm.gravar_bytes(lake.bronze_zip("202610", CARGA), montar_zip(csv, "202610"))

    tipos = {m["source"]["name"]: INTERINO_DUCKDB[m["source"]["type"]]
             for m in COPY_PARQUET["translator"]["mappings"]}
    colunas = ", ".join(f"'{n}': '{t}'" for n, t in tipos.items())
    entrada = tmp_path / "entrada.csv"
    entrada.write_bytes(csv)
    destino = Path(lake.staging("202610", CARGA)) / "inf_diario_fi_202610.parquet"
    destino.parent.mkdir(parents=True)
    duckdb.connect(":memory:").execute(f"""
        COPY (SELECT * FROM read_csv('{entrada}', header = true, delim = ';',
                                     columns = {{{colunas}}}))
        TO '{destino}' (FORMAT parquet)""")

    resposta = prova.validar(arm, lake, "202610", CARGA)
    assert resposta["ok"] is True, resposta["falhas"]


# --- o pipeline pai e os gatilhos -----------------------------------------------------------

def _meses_do_pai(parametros: dict[str, Any], agora: datetime) -> list[str]:
    """Executa o pai no avaliador: escolher_meses → ForEach → parâmetro competencia do filho."""
    params = {n: d.get("defaultValue") for n, d in PAI["parameters"].items()} | parametros
    ctx = Contexto(parametros=params, agora=agora)
    atividades = {a["name"]: a for a in PAI["activities"]}
    escolha = atividades["escolher_meses"]["typeProperties"]
    ramo = "ifTrueActivities" if avaliar(escolha["expression"], ctx) else "ifFalseActivities"
    (definir,) = escolha[ramo]
    tp = definir["typeProperties"]
    variaveis = {tp["variableName"]: avaliar(tp["value"], ctx)}
    laco = atividades["para_cada_competencia"]["typeProperties"]
    itens = avaliar(laco["items"], Contexto(parametros=params, variaveis=variaveis))
    (carregar,) = laco["activities"]
    competencia = carregar["typeProperties"]["parameters"]["competencia"]
    return [avaliar(competencia, Contexto(parametros=params, item=i, agora=agora)) for i in itens]


def _agendado(dia: date) -> str:
    """scheduledTime de um disparo às 10:00 de Brasília, em UTC como o ADF entrega."""
    local = datetime(dia.year, dia.month, dia.day, 10, 0, tzinfo=BRASILIA)
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gatilho(nome: str) -> tuple[dict[str, Any], dict[str, Any]]:
    tr = TR[nome]
    (pipeline,) = tr["pipelines"]
    assert pipeline["pipelineReference"]["referenceName"] == "pl_inf_diario"
    return tr["typeProperties"]["recurrence"], pipeline["parameters"]


DIAS = [date(2025, 12, 1) + timedelta(days=n) for n in range(0, 800, 3)]
VIRADAS = [date(2026, 1, 1), date(2026, 12, 31), date(2027, 1, 1), date(2026, 3, 1),
           date(2027, 11, 30), date(2028, 2, 29)]


@pytest.mark.parametrize(("gatilho", "esperado"),
                         [("tr_diario", JANELA_DIARIA), ("tr_semanal", JANELA_SEMANAL)])
def test_cada_gatilho_recarrega_a_janela_de_janela_py(gatilho: str,
                                                      esperado: tuple[int, int]) -> None:
    _, params = _gatilho(gatilho)
    assert (params["de"], params["ate"]) == esperado
    for dia in DIAS + VIRADAS:
        ctx = Contexto(agendado=_agendado(dia))
        p = {k: avaliar(v, ctx) for k, v in params.items()}
        agora = datetime(2030, 1, 1, tzinfo=UTC)  # ignorado: a referência vem do gatilho
        assert _meses_do_pai(p, agora) == janela(dia, *esperado), dia


def test_gatilhos_as_10h_de_brasilia_nos_dias_do_contrato() -> None:
    semana = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    for nome, dias in (("tr_diario", semana), ("tr_semanal", ["Sunday"])):
        rec, _ = _gatilho(nome)
        assert FUSOS_WINDOWS.get(rec["timeZone"]) == "America/Sao_Paulo"
        assert rec["frequency"] == "Week" and rec["interval"] == 1
        assert rec["schedule"] == {"weekDays": dias, "hours": [10], "minutes": [0]}
        assert not rec["startTime"].endswith("Z")  # com fuso fora de UTC, sem o Z
        assert TR[nome]["runtimeState"] == "Started"


def test_execucao_manual_usa_o_dia_de_brasilia_e_nao_o_de_utc() -> None:
    # 30/11 às 23:30 em Brasília já é 01/12 em UTC: M é novembro.
    agora = datetime(2026, 11, 30, 23, 30, tzinfo=BRASILIA)
    assert _meses_do_pai({}, agora) == ["202611", "202610"]
    assert _meses_do_pai({"de": 2, "ate": 11}, agora) == janela(date(2026, 11, 30), 2, 11)
    assert _meses_do_pai({"referencia": "2027-01-05T13:00:00Z"}, agora) == ["202701", "202612"]


def test_execucao_manual_com_lista_carrega_exatamente_a_lista() -> None:
    agora = datetime(2026, 11, 24, 13, 0, tzinfo=UTC)
    assert _meses_do_pai({"competencias": ["202609", "202512"]}, agora) == ["202609", "202512"]
    assert _meses_do_pai({"competencias": [202609]}, agora) == ["202609"]
    # Mês repetido na lista: duas execuções do filho no mesmo mês colidiriam na troca da prata.
    assert _meses_do_pai({"competencias": ["202609", "202512", "202609"]}, agora) == [
        "202609", "202512"]


def test_janela_invertida_falha_em_vez_de_carregar_nada() -> None:
    with pytest.raises(ErroDeExpressao):
        _meses_do_pai({"de": 3, "ate": 1}, datetime(2026, 11, 24, tzinfo=UTC))


def test_pai_uma_execucao_por_vez_e_lotes_de_quatro_meses() -> None:
    assert PAI["concurrency"] == 1
    laco = next(a for a in PAI["activities"] if a["type"] == "ForEach")["typeProperties"]
    assert laco["isSequential"] is False and laco["batchCount"] == 4
    (carregar,) = laco["activities"]
    assert carregar["typeProperties"]["waitOnCompletion"] is True
    assert carregar["typeProperties"]["parameters"]["carga"] == {
        "value": "@pipeline().RunId", "type": "Expression"}
    assert PAI["variables"]["meses"]["type"] == "Array"


# --- segurança ------------------------------------------------------------------------------

def test_nenhum_segredo_nem_id_de_assinatura_no_json() -> None:
    guid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
    for arquivo in ADF.rglob("*.json"):
        texto = arquivo.read_text(encoding="utf-8")
        assert not guid.search(texto), arquivo
        assert not re.search(r"accountKey|sasUri|sasToken|password|servicePrincipalKey", texto,
                             re.I), arquivo


def test_lake_e_cofre_pela_identidade_gerenciada_e_chave_da_function_no_cofre() -> None:
    assert LS["ls_adls_lake"]["type"] == "AzureBlobFS"
    assert set(LS["ls_adls_lake"]["typeProperties"]) == {"url"}  # sem credencial: identidade
    assert set(LS["ls_kv"]["typeProperties"]) == {"baseUrl"}
    chave = LS["ls_funcao"]["typeProperties"]["functionKey"]
    assert chave["type"] == "AzureKeyVaultSecret"
    assert chave["store"]["referenceName"] == "ls_kv"
    assert LS["ls_http_cvm"]["typeProperties"]["authenticationType"] == "Anonymous"


def test_enderecos_do_ambiente_nunca_resolvem_sem_o_parametro_do_arm() -> None:
    for nome, chave in (("ls_adls_lake", "url"), ("ls_kv", "baseUrl"),
                        ("ls_funcao", "functionAppUrl")):
        host = re.sub(r"^https://([^/]+).*$", r"\1", LS[nome]["typeProperties"][chave])
        assert host.endswith(".invalid"), (nome, host)  # RFC 2606


# --- o ARM exportado (ci/adf/validar.sh) ---------------------------------------------------

ARM = Path(os.environ["ADF_ARM"]) if os.environ.get("ADF_ARM") else None
sem_arm = pytest.mark.skipif(ARM is None, reason="ADF_ARM não definido (rode ci/adf/validar.sh)")

PARAMETROS_DO_DEPLOY = {
    "factoryName",
    "ls_adls_lake_properties_typeProperties_url",
    "ls_kv_properties_typeProperties_baseUrl",
    "ls_funcao_properties_typeProperties_functionAppUrl",
}


@sem_arm
def test_arm_tem_um_parametro_para_cada_endereco_do_ambiente() -> None:
    assert ARM is not None
    params = json.loads((ARM / "ARMTemplateParametersForFactory.json").read_text())["parameters"]
    assert set(params) >= PARAMETROS_DO_DEPLOY
    adf_md = (RAIZ / "docs" / "adf.md").read_text(encoding="utf-8")
    for nome in PARAMETROS_DO_DEPLOY:
        assert f"`{nome}`" in adf_md, f"{nome} sem a origem documentada em docs/adf.md"
    template = json.loads((ARM / "ARMTemplateForFactory.json").read_text())
    assert not [p for p, d in template["parameters"].items() if d["type"] == "secureString"]


@sem_arm
def test_arm_nao_recria_a_fabrica_que_e_do_terraform() -> None:
    assert ARM is not None
    template = json.loads((ARM / "ARMTemplateForFactory.json").read_text())
    tipos = {r["type"] for r in template["resources"]}
    assert "Microsoft.DataFactory/factories" not in tipos  # D9: a fábrica e a identidade
    assert {"Microsoft.DataFactory/factories/pipelines",
            "Microsoft.DataFactory/factories/triggers"} <= tipos
