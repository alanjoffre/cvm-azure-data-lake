"""De onde vem o ZIP de cada mês: o servidor da CVM (HTTP) ou uma pasta com uma cópia fixa.

A cópia fixa (snapshot) existe para a prova ser repetível: a CVM regrava a janela todo dia, e a
prova precisa saber exatamente qual versão entrou (T0) e qual a substituiu (T1).
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cvm_lake.lake import conferir_competencia

URL_BASE_CVM = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS"
# Identificação genérica do cliente, sem dado pessoal.
USER_AGENT = "cvm-azure-data-lake/0.1"


def nome_arquivo(competencia: str) -> str:
    """Nome do ZIP do mês no portal da CVM (inf_diario_fi_AAAAMM.zip)."""
    return f"inf_diario_fi_{conferir_competencia(competencia)}.zip"


@dataclass(frozen=True)
class Metadados:
    """O que o manifesto guarda da origem (D3: o ETag serve à otimização futura)."""

    url: str
    etag: str | None
    last_modified: str | None
    bytes: int
    sha256_zip: str
    obtido_em: str


@dataclass(frozen=True)
class Arquivo:
    conteudo: bytes
    metadados: Metadados


class Origem(Protocol):
    def baixar(self, competencia: str) -> Arquivo: ...


def metadados_de(
    url: str, conteudo: bytes, etag: str | None, last_modified: str | None
) -> Metadados:
    """Metadados HTTP de um download (URL, ETag, Last-Modified), com o tamanho e o hash do ZIP."""
    return Metadados(
        url=url,
        etag=etag,
        last_modified=last_modified,
        bytes=len(conteudo),
        sha256_zip=hashlib.sha256(conteudo).hexdigest(),
        obtido_em=datetime.now(UTC).isoformat(timespec="seconds"),
    )


class OrigemHTTP:
    """O servidor de dados abertos da CVM (ou outro com o mesmo layout de URL)."""

    def __init__(self, url_base: str = URL_BASE_CVM, timeout: float = 120.0) -> None:
        self.url_base = url_base.rstrip("/")
        self.timeout = timeout

    def baixar(self, competencia: str) -> Arquivo:
        url = f"{self.url_base}/{nome_arquivo(competencia)}"
        pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
            conteudo: bytes = resposta.read()
            etag = resposta.headers.get("ETag")
            last_modified = resposta.headers.get("Last-Modified")
            tamanho = resposta.headers.get("Content-Length")
        if tamanho is not None and int(tamanho) != len(conteudo):
            raise OSError(f"{url}: recebidos {len(conteudo)} bytes de {tamanho}")
        return Arquivo(conteudo, metadados_de(url, conteudo, etag, last_modified))

    def cabecalhos(self, competencia: str) -> dict[str, Any]:
        """``HEAD`` na URL do mês: os metadados que o manifesto guarda, sem baixar o arquivo (D3).

        Na Azure, quem baixa é o Copy do ADF, que não expõe os cabeçalhos da resposta. A Function
        faz o ``HEAD`` na reconciliação, minutos depois da cópia.
        """
        url = f"{self.url_base}/{nome_arquivo(competencia)}"
        pedido = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
            tamanho = resposta.headers.get("Content-Length")
            return {
                "url": url,
                "etag": resposta.headers.get("ETag"),
                "last_modified": resposta.headers.get("Last-Modified"),
                "bytes": int(tamanho) if tamanho is not None else None,
                "obtido_em": datetime.now(UTC).isoformat(timespec="seconds"),
            }


class OrigemPasta:
    """Uma pasta com ``inf_diario_fi_AAAAMM.zip`` e, ao lado, ``.origem.json`` com os metadados."""

    def __init__(self, pasta: Path) -> None:
        self.pasta = pasta

    def baixar(self, competencia: str) -> Arquivo:
        caminho = self.pasta / nome_arquivo(competencia)
        conteudo = caminho.read_bytes()
        lateral = caminho.with_suffix(".origem.json")
        if lateral.exists():
            dado = json.loads(lateral.read_text(encoding="utf-8"))
            meta = Metadados(**dado)
            if meta.sha256_zip != hashlib.sha256(conteudo).hexdigest():
                raise OSError(f"{caminho}: o ZIP não é o descrito em {lateral.name}")
            return Arquivo(conteudo, meta)
        return Arquivo(conteudo, metadados_de(caminho.resolve().as_uri(), conteudo, None, None))


def guardar(arquivo: Arquivo, pasta: Path, competencia: str) -> Path:
    """Grava o ZIP e os metadados numa pasta de snapshot (lida depois por ``OrigemPasta``)."""
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / nome_arquivo(competencia)
    destino.write_bytes(arquivo.conteudo)
    destino.with_suffix(".origem.json").write_text(
        json.dumps(asdict(arquivo.metadados), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return destino
