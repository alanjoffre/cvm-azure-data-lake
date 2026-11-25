"""O lake no ADLS Gen2, pelo SDK, com a identidade gerenciada da Function.

Na Azure, a raiz do ``Lake`` é o endpoint dfs da conta (``https://<conta>.dfs.core.windows.net``).
O primeiro segmento depois dela é o container (a camada) e o resto é o caminho dentro dele: é a
própria URL do arquivo no serviço.

O DuckDB lê Parquet do disco. Os Parquet da pasta pedida são baixados para um diretório
temporário da instância, apagado ao fechar. A Function não depende de instalar a extensão
``azure`` do DuckDB em tempo de execução, e lê e grava pelo mesmo cliente (D2b).

Erros do serviço (arquivo ausente, permissão negada) viram ``OSError``: ``prova.py`` os registra
como falha da carga (``ok: false``), com a mensagem do serviço, em vez de derrubar a Function.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

from azure.core.exceptions import AzureError
from azure.storage.filedatalake import DataLakeFileClient, DataLakeServiceClient


@contextmanager
def _como_oserror(caminho: str) -> Iterator[None]:
    try:
        yield
    except AzureError as erro:
        raise OSError(f"{caminho}: {type(erro).__name__}: {erro}") from erro


class ArmazenamentoADLS:
    """ADLS Gen2 com a identidade gerenciada da Function; grava por arquivo temporário e rename
    atômico, para quem lê nunca ver um arquivo pela metade."""

    def __init__(self, servico: DataLakeServiceClient) -> None:
        self._servico = servico
        self.raiz = servico.url.rstrip("/")
        self._tmp = tempfile.TemporaryDirectory(prefix="lake-")
        self._downloads = 0

    @classmethod
    def da_conta(cls, conta: str) -> ArmazenamentoADLS:
        """Cliente da conta do lake com a identidade gerenciada do sistema, sem outra credencial."""
        from azure.identity import ManagedIdentityCredential

        url = f"https://{conta}.dfs.core.windows.net"
        return cls(DataLakeServiceClient(url, credential=ManagedIdentityCredential()))

    def __enter__(self) -> ArmazenamentoADLS:
        return self

    def __exit__(
        self,
        tipo: type[BaseException] | None,
        erro: BaseException | None,
        rastro: TracebackType | None,
    ) -> None:
        self.fechar()

    def fechar(self) -> None:
        self._tmp.cleanup()

    def dividir(self, caminho: str) -> tuple[str, str]:
        """``<raiz>/<container>/<caminho>`` → (container, caminho). Fora da raiz é erro."""
        prefixo = self.raiz + "/"
        container, _, resto = caminho.removeprefix(prefixo).partition("/")
        if not caminho.startswith(prefixo) or not container or not resto:
            raise ValueError(f"caminho fora do lake {self.raiz}: {caminho!r}")
        return container, resto

    def _arquivo(self, caminho: str) -> DataLakeFileClient:
        container, resto = self.dividir(caminho)
        return self._servico.get_file_client(container, resto)

    def ler_bytes(self, caminho: str) -> bytes:
        with _como_oserror(caminho):
            return self._arquivo(caminho).download_file().readall()

    def gravar_bytes(self, caminho: str, conteudo: bytes) -> None:
        # upload_data(overwrite=True) trunca o arquivo antes de regravar: no meio, quem lê veria
        # o arquivo vazio, e uma falha o deixaria vazio. Grava num nome temporário ao lado e
        # renomeia por cima do destino (o rename do ADLS Gen2 sobrescreve por padrão). O nome
        # temporário não termina em .parquet, então nenhum glob de partição o enxerga.
        container, resto = self.dividir(caminho)
        temporario = self._servico.get_file_client(container, f"{resto}.tmp-{uuid.uuid4().hex}")
        with _como_oserror(caminho):
            temporario.upload_data(conteudo, overwrite=True)
            temporario.rename_file(f"{container}/{resto}")

    def existe(self, caminho: str) -> bool:
        with _como_oserror(caminho):
            return bool(self._arquivo(caminho).exists())

    def tamanho(self, caminho: str) -> int:
        with _como_oserror(caminho):
            return int(self._arquivo(caminho).get_file_properties().size)

    def caminho_duckdb(self, caminho: str) -> str:
        """Baixa os Parquet de ``<pasta>/*.parquet`` (``lake.parquets``) e devolve o glob local.

        Pasta existente sem Parquet devolve um glob vazio, e o DuckDB acusa "No files found",
        como na pasta local; pasta inexistente é ``OSError``.
        """
        pasta, _, padrao = caminho.rpartition("/")
        if padrao != "*.parquet":
            raise ValueError(f"esperado <pasta>/*.parquet: {caminho!r}")
        container, diretorio = self.dividir(pasta)
        destino = Path(self._tmp.name) / str(self._downloads)
        self._downloads += 1
        destino.mkdir()
        sistema = self._servico.get_file_system_client(container)
        with _como_oserror(pasta):
            for item in sistema.get_paths(path=diretorio, recursive=False):
                nome = str(item.name)
                if item.is_directory or not nome.endswith(".parquet"):
                    continue
                conteudo = sistema.get_file_client(nome).download_file().readall()
                (destino / nome.rsplit("/", 1)[-1]).write_bytes(conteudo)
        return f"{destino}/*.parquet"
