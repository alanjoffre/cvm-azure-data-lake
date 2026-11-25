"""Monta o pacote da Function (saida/funcao.zip) para o deploy no Flex Consumption.

Layout que a doc do Python no Azure Functions pede, na raiz do zip, sem a pasta-mãe:
``function_app.py``, ``host.json``, ``requirements.txt`` e o pacote ``cvm_lake``. As dependências
não vão no zip: o deploy pede o build remoto, que instala o ``requirements.txt`` no Linux da
Function (``ci/deploy/funcao.sh``).

O ``requirements.txt`` sai do ``uv.lock`` com os hashes: o build remoto instala as mesmas
versões que os testes usaram e recusa um pacote adulterado. O zip é reprodutível (ordem e data
fixas): o mesmo commit gera o mesmo sha256.

    uv run python ci/funcao/empacotar.py      # grava saida/funcao.zip e imprime o sha256
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DATA_FIXA = (1980, 1, 1, 0, 0, 0)


def requisitos() -> str:
    return subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--extra",
            "azure",
            "--no-emit-project",
            "--no-header",
            "--quiet",
        ],
        cwd=RAIZ,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def arquivos(raiz: Path) -> dict[str, bytes]:
    """Caminho no zip → conteúdo. Só o código: nada de cache, teste ou dado."""
    conteudo = {
        "function_app.py": (raiz / "funcao_azure" / "function_app.py").read_bytes(),
        "host.json": (raiz / "funcao_azure" / "host.json").read_bytes(),
    }
    pacote = raiz / "src" / "cvm_lake"
    for arquivo in sorted(pacote.rglob("*")):
        if (
            arquivo.is_file()
            and "__pycache__" not in arquivo.parts
            and arquivo.suffix in {".py", ".sql", ".typed"}
        ):
            conteudo[f"cvm_lake/{arquivo.relative_to(pacote).as_posix()}"] = arquivo.read_bytes()
    return conteudo


def empacotar(raiz: Path, destino: Path, requirements: str) -> str:
    """Grava o zip e devolve o sha256."""
    conteudo = arquivos(raiz) | {"requirements.txt": requirements.encode()}
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for nome in sorted(conteudo):
            info = zipfile.ZipInfo(nome, DATA_FIXA)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, conteudo[nome])
    return hashlib.sha256(destino.read_bytes()).hexdigest()


def main() -> int:
    destino = RAIZ / "saida" / "funcao.zip"
    sha = empacotar(RAIZ, destino, requisitos())
    print(f"{destino.relative_to(RAIZ)} sha256={sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
