"""O substituto local do ADF: os 7 passos, a bronze imutável e a falha que não toca a prata."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from conftest import COMPETENCIAS, linhas_csv, montar_csv, montar_zip, zip_sintetico

from cvm_lake import adf_local, esquema, prova_completa
from cvm_lake.lake import Lake, parquets
from cvm_lake.origem import OrigemPasta

POS = {nome: i for i, nome in enumerate(esquema.NOMES)}


def _manifesto(lake: Lake, competencia: str) -> dict[str, object]:
    dado: dict[str, object] = json.loads(Path(lake.manifesto(competencia)).read_text())
    return dado


def test_prova_completa_passa_no_sintetico(snapshot: Path, tmp_path: Path) -> None:
    relatorio = prova_completa.executar(snapshot, COMPETENCIAS, tmp_path / "trabalho")
    falhas = [c for c in relatorio["checagens"] if not c["ok"]]
    assert falhas == []
    assert relatorio["ok"] is True
    nomes = " ".join(c["nome"] for c in relatorio["checagens"])
    for etapa in ("T0", "reexecução", "T1", "divergência", "referência Python"):
        assert etapa in nomes
    assert relatorio["republicada"] == "202610"
    assert {t["carga"] for t in relatorio["tempos"]} == {"T0", "reexecução", "T1"}


def test_prova_recusa_pasta_de_trabalho_com_conteudo(snapshot: Path, tmp_path: Path) -> None:
    (tmp_path / "sujo").mkdir()
    (tmp_path / "sujo" / "x").write_text("x")
    with pytest.raises(ValueError, match="vazio"):
        prova_completa.executar(snapshot, COMPETENCIAS, tmp_path / "sujo")


def test_bronze_e_imutavel(snapshot: Path, tmp_path: Path) -> None:
    lake = Lake(str(tmp_path / "lake"))
    adf_local.pl_inf_diario_mes(OrigemPasta(snapshot), lake, "202609", "carga-1")
    with pytest.raises(FileExistsError):
        adf_local.pl_inf_diario_mes(OrigemPasta(snapshot), lake, "202609", "carga-1")


def test_divergencia_na_staging_nao_toca_a_prata(snapshot: Path, tmp_path: Path) -> None:
    """A falha da validação deixa a prata e o manifesto como estavam."""
    lake = Lake(str(tmp_path / "lake"))
    adf_local.pl_inf_diario_mes(OrigemPasta(snapshot), lake, "202610", "boa")
    antes = _manifesto(lake, "202610")
    prata_antes = sorted(p.read_bytes() for p in Path(lake.prata("202610")).glob("*.parquet"))

    with pytest.raises(adf_local.CargaFalhou) as erro:
        adf_local.pl_inf_diario_mes(
            OrigemPasta(snapshot),
            lake,
            "202610",
            "ruim",
            depois_da_staging=prova_completa.corromper_staging,
        )

    assert erro.value.resposta["etapa"] == "validar"
    assert _manifesto(lake, "202610") == antes
    assert (
        sorted(p.read_bytes() for p in Path(lake.prata("202610")).glob("*.parquet")) == prata_antes
    )
    relatorio = json.loads(Path(lake.relatorio_carga("202610", "ruim")).read_text())
    assert relatorio["validacao"]["ok"] is False
    assert Path(lake.staging("202610", "ruim")).exists()


def test_um_mes_ruim_nao_impede_os_outros(
    gravar_snapshot: Callable[[dict[str, bytes]], Path], tmp_path: Path
) -> None:
    linhas = [list(linha) for linha in linhas_csv("202610")]
    linhas[0][POS["VL_QUOTA"]] = "1.1234567890123"  # 13 casas: fora do dicionário
    pasta = gravar_snapshot(
        {
            "202609": zip_sintetico("202609"),
            "202610": montar_zip(montar_csv(linhas), "202610"),
            "202611": zip_sintetico("202611"),
        }
    )
    lake = Lake(str(tmp_path / "lake"))
    with pytest.raises(ExceptionGroup) as grupo:
        adf_local.pl_inf_diario(OrigemPasta(pasta), lake, COMPETENCIAS, "carga-x")
    assert len(grupo.value.exceptions) == 1
    assert "fora do dicionário" in str(grupo.value.exceptions[0])
    assert Path(lake.manifesto("202609")).exists()
    assert Path(lake.manifesto("202611")).exists()
    assert not Path(lake.manifesto("202610")).exists()


def test_ouro_com_valores_conhecidos(
    gravar_snapshot: Callable[[dict[str, bytes]], Path], tmp_path: Path
) -> None:
    base = ["FI", "11.111.111/0001-11", ""]
    linhas = [
        [*base, "2026-10-01", "100.00", "2.000000000000", "1000.00", "10.00", "1.00", "5"],
        [*base, "2026-10-02", "100.00", "2.500000000000", "1100.00", "20.00", "2.00", "6"],
        [*base, "2026-10-05", "100.00", "3.000000000000", "1200.00", "30.00", "3.00", "7"],
        [
            "FI",
            "22.222.222/0001-22",
            "",
            "2026-10-01",
            "0.00",
            "0.000000000000",
            "0.00",
            "0.00",
            "0.00",
            "0",
        ],
    ]
    pasta = gravar_snapshot({"202610": montar_zip(montar_csv(linhas), "202610")})
    lake = Lake(str(tmp_path / "lake"))
    adf_local.pl_inf_diario_mes(OrigemPasta(pasta), lake, "202610", "c")
    con = duckdb.connect(":memory:")
    ouro = con.execute(f"""
        SELECT cnpj_fundo_classe, dias_com_informe, CAST(pl_ultimo_dia AS VARCHAR),
               cotistas_ultimo_dia, CAST(captacao AS VARCHAR), CAST(resgate AS VARCHAR),
               CAST(captacao_liquida AS VARCHAR), variacao_cota, primeiro_dia::VARCHAR,
               ultimo_dia::VARCHAR
        FROM read_parquet('{parquets(lake.ouro("202610"))}') ORDER BY 1""").fetchall()
    assert ouro[0] == (
        "11.111.111/0001-11",
        3,
        "1200.00",
        7,
        "60.00",
        "6.00",
        "54.00",
        0.5,
        "2026-10-01",
        "2026-10-05",
    )
    assert ouro[1][7] is None  # cota inicial zero: sem variação


def test_copy_que_quebra_nao_impede_os_outros_meses(
    gravar_snapshot: Callable[[dict[str, bytes]], Path], tmp_path: Path
) -> None:
    import io
    import zipfile

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as arquivo:  # dois CSVs: layout novo da CVM
        arquivo.writestr("a.csv", b"x")
        arquivo.writestr("b.csv", b"y")
    pasta = gravar_snapshot({"202609": saida.getvalue(), "202610": zip_sintetico("202610")})
    lake = Lake(str(tmp_path / "lake"))
    with pytest.raises(ExceptionGroup) as grupo:
        adf_local.pl_inf_diario(OrigemPasta(pasta), lake, ["202609", "202610"], "c")
    assert [type(e).__name__ for e in grupo.value.exceptions] == ["ErroDeLayout"]
    assert Path(lake.manifesto("202610")).exists()
    assert not Path(lake.prata("202609")).exists()


def test_cli_prova_grava_o_relatorio(snapshot: Path, tmp_path: Path) -> None:
    from cvm_lake import cli

    codigo = cli.main(
        [
            "prova",
            "--origem",
            str(snapshot),
            "--trabalho",
            str(tmp_path / "t"),
            "--relatorio",
            str(tmp_path / "r"),
        ]
    )
    assert codigo == 0
    relatorio = json.loads((tmp_path / "r" / "prova.json").read_text())
    assert relatorio["ok"] is True and relatorio["competencias"] == COMPETENCIAS
    texto = (tmp_path / "r" / "prova.md").read_text()
    assert "Nada foi aplicado na Azure" in texto and "FALHOU" not in texto
    # Tempo com vírgula decimal: "2.53 s" seria lido como 2.530 segundos num texto em pt-BR.
    maior = re.search(r"Maior tempo da Function \(as duas chamadas somadas\): (\S+) s\.", texto)
    assert maior and re.fullmatch(r"\d+,\d{2}", maior.group(1))


def test_linha_de_outro_mes_no_arquivo_derruba_o_mes(
    gravar_snapshot: Callable[[dict[str, bytes]], Path], tmp_path: Path
) -> None:
    # A chave só é única dentro da partição: uma linha de setembro no arquivo de outubro
    # duplicaria setembro no lake inteiro. A validação confere a premissa.
    linhas = [list(linha) for linha in linhas_csv("202610")]
    linhas[0][POS["DT_COMPTC"]] = "2026-09-30"
    pasta = gravar_snapshot(
        {
            "202609": zip_sintetico("202609"),
            "202610": montar_zip(montar_csv(linhas), "202610"),
            "202611": zip_sintetico("202611"),
        }
    )
    lake = Lake(str(tmp_path / "lake"))
    with pytest.raises(ExceptionGroup) as grupo:
        adf_local.pl_inf_diario(OrigemPasta(pasta), lake, COMPETENCIAS, "carga-x")
    assert "1 linha com DT_COMPTC fora de 202610" in str(grupo.value.exceptions[0])
    assert not Path(lake.manifesto("202610")).exists()
