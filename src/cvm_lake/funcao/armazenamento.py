"""O que a Function precisa do armazenamento, e a implementação em pasta local.

A Function só lê bytes (o ZIP da bronze), lê Parquet pelo DuckDB e grava bytes (JSON do controle
e o Parquet do ouro). Todo o resto (apagar e copiar partição) é trabalho do ADF.

A implementação para o ADLS Gen2 está em ``adls.py`` (SDK ``azure-storage-file-datalake`` com a
identidade gerenciada). Ela cumpre o mesmo protocolo, e por isso nada em ``prova.py`` muda.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Armazenamento(Protocol):
    def ler_bytes(self, caminho: str) -> bytes: ...

    def gravar_bytes(self, caminho: str, conteudo: bytes) -> None: ...

    def existe(self, caminho: str) -> bool: ...

    def tamanho(self, caminho: str) -> int: ...

    def caminho_duckdb(self, caminho: str) -> str:
        """Caminho (ou glob) no formato que o ``read_parquet`` do DuckDB entende."""
        ...


class ArmazenamentoLocal:
    """Pasta local com o layout do lake. A gravação é atômica (arquivo temporário + rename)."""

    def ler_bytes(self, caminho: str) -> bytes:
        return Path(caminho).read_bytes()

    def gravar_bytes(self, caminho: str, conteudo: bytes) -> None:
        destino = Path(caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporario = destino.with_name(f".{destino.name}.tmp")
        temporario.write_bytes(conteudo)
        temporario.replace(destino)

    def existe(self, caminho: str) -> bool:
        return Path(caminho).exists()

    def tamanho(self, caminho: str) -> int:
        return Path(caminho).stat().st_size

    def caminho_duckdb(self, caminho: str) -> str:
        return caminho
