"""Linha de comando: ``cvm-lake baixar`` (snapshot T0 da CVM) e ``cvm-lake prova`` (relatório).

uv run cvm-lake baixar --competencias 202609 202610 202611 --destino dados/origem/T0
uv run cvm-lake prova --origem dados/origem/T0 --trabalho saida/prova --relatorio reports
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb

from cvm_lake import prova_completa
from cvm_lake.origem import OrigemHTTP, guardar

# Uma Function disparada por HTTP tem 230 s para responder (idle timeout do Azure Load Balancer),
# qualquer que seja o functionTimeout: é o limite de cada chamada da atividade do ADF.
LIMITE_FUNCAO_S = 230


def _git(*args: str) -> str:
    try:
        saida = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return ""
    return saida.stdout.strip()


def _ambiente() -> dict[str, Any]:
    sujo = bool(_git("status", "--porcelain", "--", "src", "tests", "pyproject.toml"))
    return {
        "commit": _git("rev-parse", "HEAD"),
        "codigo_com_alteracao_local": sujo,
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "sistema": f"{platform.system()} {platform.machine()}",
    }


def _baixar(args: argparse.Namespace) -> int:
    origem = OrigemHTTP(args.url_base)
    for competencia in args.competencias:
        arquivo = origem.baixar(competencia)
        destino = guardar(arquivo, Path(args.destino), competencia)
        m = arquivo.metadados
        print(
            f"{competencia}: {m.bytes} bytes, Last-Modified {m.last_modified}, "
            f"ETag {m.etag} → {destino}"
        )
    return 0


_mil = prova_completa.mil


def _seg(s: float) -> str:
    """Segundos com vírgula decimal: no texto em pt-BR, o ponto é o separador de milhar."""
    return f"{s:.2f}".replace(".", ",")


def para_markdown(r: dict[str, Any]) -> str:
    """Monta reports/prova.md a partir do dicionário que vai para reports/prova.json."""
    amb = r["ambiente"]
    ok = sum(c["ok"] for c in r["checagens"])
    linhas = [
        "# Relatório da prova local",
        "",
        "Gerado por `uv run cvm-lake prova` em " + r["gerado_em"] + ". Este relatório mede o "
        "substituto local (DuckDB e pastas). Nada foi aplicado na Azure.",
        "",
        f"- Resultado: **{'passou' if r['ok'] else 'FALHOU'}** ({ok} de "
        f"{len(r['checagens'])} checagens)",
        f"- Código: commit `{amb['commit'][:12] or 'sem git'}`"
        + (" com alteração local" if amb["codigo_com_alteracao_local"] else ""),
        f"- Ambiente: Python {amb['python']}, DuckDB {amb['duckdb']}, {amb['sistema']}",
        f"- Mês republicado na simulação: {r['republicada']}",
        "",
        "## Os arquivos (snapshot T0)",
        "",
        "| Competência | Linhas | ZIP (bytes) | CSV (bytes) | Fundos/classes | Pares fundo + "
        "subclasse | Datas | Sem subclasse | Last-Modified | ETag |",
        "|---|---:|---:|---:|---:|---:|---|---:|---|---|",
    ]
    for c, m in r["meses"].items():
        p, o = m["perfil"], m["origem"]
        linhas.append(
            f"| {c} | {_mil(p['linhas'])} | {_mil(p['bytes_zip'])} | {_mil(p['bytes_csv'])} | "
            f"{_mil(p['fundos'])} | {_mil(p['pares_fundo_subclasse'])} | {p['datas']} "
            f"({p['primeira_data']} a {p['ultima_data']}) | {_mil(p['linhas_sem_subclasse'])} | "
            f"{o['last_modified'] or '—'} | `{o['etag'] or '—'}` |"
        )
    linhas += ["", "SHA-256 canônico de cada mês em T0 (DuckDB e referência Python):", ""]
    for c, m in r["meses"].items():
        linhas.append(f"- {c}: `{m['sha256_t0']}`")
    rep = r["republicacao"]
    linhas += [
        "",
        "## Republicação simulada",
        "",
        f"T1 de {rep['competencia']}: {len(rep['alteradas'])} linhas alteradas (+1.000,00 em "
        f"`VL_PATRIM_LIQ` e `CAPTC_DIA`), 1 removida e 1 incluída. A mesma contagem de T0 "
        f"({_mil(rep['linhas_t1'])}), hash `{rep['sha256_t1'][:16]}…`.",
        "",
        "## Checagens",
        "",
        "| | Checagem | Detalhe |",
        "|---|---|---|",
    ]
    for c in r["checagens"]:
        detalhe = c["detalhe"].replace("|", "\\|")
        linhas.append(f"| {'ok' if c['ok'] else 'FALHOU'} | {c['nome']} | {detalhe} |")
    linhas += [
        "",
        "## Tempos por mês",
        "",
        "Function (s) = validar + reconciliar, as duas chamadas que a Function fará. Medido nesta "
        f"máquina, não na Azure: a folga contra os {LIMITE_FUNCAO_S} s que uma Function por HTTP "
        "tem para responder se confirma na execução na Azure.",
        "",
        "| Carga | Competência | Pipeline (s) | Validar (s) | Reconciliar (s) | Function (s) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for t in r["tempos"]:
        linhas.append(
            f"| {t['carga']} | {t['competencia']} | {_seg(t['pipeline_s'])} | "
            f"{_seg(t['validar_s'])} | {_seg(t['reconciliar_s'])} | "
            f"{_seg(t['funcao_s'])} |"
        )
    linhas += [
        "",
        f"Maior tempo da Function (as duas chamadas somadas): {_seg(r['funcao_s_max'])} s. "
        f"Cada chamada HTTP tem até {LIMITE_FUNCAO_S} s para responder.",
        "",
    ]
    return "\n".join(linhas)


def _prova(args: argparse.Namespace) -> int:
    trabalho = Path(args.trabalho)
    if args.limpar and trabalho.exists():
        shutil.rmtree(trabalho)
    competencias = args.competencias or sorted(
        p.name.removeprefix("inf_diario_fi_").removesuffix(".zip")
        for p in Path(args.origem).glob("inf_diario_fi_*.zip")
    )
    relatorio = prova_completa.executar(Path(args.origem), competencias, trabalho, args.republicar)
    relatorio["ambiente"] = _ambiente()
    pasta = Path(args.relatorio)
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "prova.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (pasta / "prova.md").write_text(para_markdown(relatorio), encoding="utf-8")
    falhas = [c for c in relatorio["checagens"] if not c["ok"]]
    print(
        f"{len(relatorio['checagens']) - len(falhas)} de {len(relatorio['checagens'])} "
        f"checagens ok; relatório em {pasta / 'prova.md'}"
    )
    for c in falhas:
        print(f"FALHOU: {c['nome']}: {c['detalhe']}", file=sys.stderr)
    return 0 if relatorio["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    """Entrada do comando cvm-lake; devolve o código de saída (0 = passou)."""
    parser = argparse.ArgumentParser(prog="cvm-lake", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="comando", required=True)

    b = sub.add_parser("baixar", help="baixa os ZIPs da CVM para uma pasta de snapshot")
    b.add_argument("--competencias", nargs="+", required=True)
    b.add_argument("--destino", default="dados/origem/T0")
    b.add_argument("--url-base", default=OrigemHTTP().url_base)
    b.set_defaults(funcao=_baixar)

    p = sub.add_parser("prova", help="roda a prova completa e grava o relatório")
    p.add_argument("--origem", default="dados/origem/T0")
    p.add_argument("--competencias", nargs="*")
    p.add_argument("--republicar", default=None)
    p.add_argument("--trabalho", default="saida/prova")
    p.add_argument("--relatorio", default="reports")
    p.add_argument("--limpar", action="store_true", help="apaga a pasta de trabalho antes")
    p.set_defaults(funcao=_prova)

    args = parser.parse_args(argv)
    codigo: int = args.funcao(args)
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
