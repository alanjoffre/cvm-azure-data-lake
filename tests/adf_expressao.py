"""Avaliador mínimo da linguagem de expressões do ADF, para os testes do JSON (``adf/``).

O ADF não tem emulador (D13). Este avaliador cobre só as funções que o JSON do repositório usa,
para que os testes calculem caminhos, competências e corpos a partir do próprio JSON e comparem
com o código Python (``lake.py``, ``janela.py``, ``origem.py``, ``prova.py``). Uma função fora da
lista é erro: uma expressão nova no JSON exige o teste dela.

Duas escolhas deixam o avaliador mais rígido que o ADF, nunca mais permissivo:
- todos os argumentos são avaliados antes da chamada, inclusive os dois ramos do ``if``. A
  documentação não diz que o ``if`` do ADF avalia só um ramo, então uma expressão que só funciona
  com avaliação preguiçosa reprova aqui;
- os tipos que o validador do ADF recusa também são recusados aqui: ``if`` que devolve array e
  ``string`` de array ou objeto.

O que fica fora: interpolação ``@{...}`` e o escape ``@@`` (o JSON não usa) e a semântica exata
de fuso e de formato do .NET, reduzida aos formatos ``o`` e ``yyyyMM``. O comportamento do ADF
executando só se confirma na Azure (docs/testes.md, "Só na Azure").
"""

from __future__ import annotations

import calendar
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

# Nome do fuso no Windows (o que o gatilho e o convertFromUtc do ADF aceitam) → nome IANA.
FUSOS_WINDOWS = {"E. South America Standard Time": "America/Sao_Paulo", "UTC": "UTC"}


class ErroDeExpressao(Exception):
    pass


@dataclass(frozen=True)
class Contexto:
    parametros: Mapping[str, Any] = field(default_factory=dict)
    run_id: str = "5b0c7a3e-1d2f-4e6a-9c8b-7a6f5e4d3c2b"
    item: Any = None
    dataset: Mapping[str, Any] = field(default_factory=dict)
    variaveis: Mapping[str, Any] = field(default_factory=dict)
    atividades: Mapping[str, Any] = field(default_factory=dict)
    agendado: str | None = None  # trigger().scheduledTime
    agora: datetime = datetime(2026, 11, 24, 13, 0, tzinfo=UTC)


# --- leitura -------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<nome>[A-Za-z_]\w*)|'(?P<str>(?:[^']|'')*)'"
    r"|(?P<sinal>[().,\[\]]))"
)


def _tokens(texto: str) -> list[tuple[str, str]]:
    saida: list[tuple[str, str]] = []
    pos = 0
    while pos < len(texto):
        if texto[pos:].strip() == "":
            break
        m = _TOKEN.match(texto, pos)
        if not m or m.end() == pos:
            raise ErroDeExpressao(f"não sei ler a partir de: {texto[pos:]!r}")
        tipo = m.lastgroup
        assert tipo is not None
        valor = m.group(tipo)
        saida.append((tipo, valor.replace("''", "'") if tipo == "str" else valor))
        pos = m.end()
    return saida


@dataclass
class _Leitor:
    tokens: list[tuple[str, str]]
    pos: int = 0

    def espiar(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def tomar(self, sinal: str | None = None) -> tuple[str, str]:
        t = self.espiar()
        if t is None or (sinal is not None and t != ("sinal", sinal)):
            raise ErroDeExpressao(f"esperado {sinal!r}, veio {t!r}")
        self.pos += 1
        return t


# Árvore: ("lit", valor) | ("chamar", nome, [args]) | ("prop", alvo, nome) | ("indice", alvo, expr)
No = tuple[Any, ...]


def _expr(leitor: _Leitor) -> No:
    tipo, valor = leitor.tomar()
    no: No
    if tipo == "num":
        no = ("lit", float(valor) if "." in valor else int(valor))
    elif tipo == "str":
        no = ("lit", valor)
    elif tipo == "nome" and leitor.espiar() == ("sinal", "("):
        leitor.tomar("(")
        args: list[No] = []
        if leitor.espiar() != ("sinal", ")"):
            args.append(_expr(leitor))
            while leitor.espiar() == ("sinal", ","):
                leitor.tomar(",")
                args.append(_expr(leitor))
        leitor.tomar(")")
        no = ("chamar", valor, args)
    elif tipo == "nome" and valor in ("true", "false", "null"):
        no = ("lit", {"true": True, "false": False, "null": None}[valor])
    else:
        raise ErroDeExpressao(f"termo inesperado: {valor!r}")
    while leitor.espiar() in (("sinal", "."), ("sinal", "[")):
        if leitor.tomar()[1] == ".":
            no = ("prop", no, leitor.tomar()[1])
        else:
            no = ("indice", no, _expr(leitor))
            leitor.tomar("]")
    return no


def ler(texto: str) -> No:
    if not texto.startswith("@") or texto.startswith("@@") or "@{" in texto:
        raise ErroDeExpressao(f"só expressões inteiras com '@' no início: {texto!r}")
    leitor = _Leitor(_tokens(texto[1:]))
    no = _expr(leitor)
    if leitor.espiar() is not None:
        raise ErroDeExpressao(f"sobrou texto em {texto!r}")
    return no


def funcoes_usadas(texto: str) -> set[str]:
    nomes: set[str] = set()

    def visitar(no: No) -> None:
        if no[0] == "chamar":
            nomes.add(no[1])
            for a in no[2]:
                visitar(a)
        elif no[0] in ("prop", "indice"):
            visitar(no[1])
            if no[0] == "indice":
                visitar(no[2])

    visitar(ler(texto))
    return nomes


# --- data e hora ----------------------------------------------------------------------------


def _data(texto: Any) -> datetime:
    if not isinstance(texto, str):
        raise ErroDeExpressao(f"esperado timestamp em texto, veio {texto!r}")
    m = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))?(Z|[+-]\d{2}:\d{2})?", texto
    )
    if not m:
        raise ErroDeExpressao(f"timestamp inválido: {texto!r}")
    base = datetime.fromisoformat(m.group(1)).replace(
        microsecond=int((m.group(2) or "0").ljust(6, "0")[:6])
    )
    if m.group(3) == "Z":
        return base.replace(tzinfo=UTC)
    if m.group(3):
        return datetime.fromisoformat(m.group(1) + m.group(3)).replace(microsecond=base.microsecond)
    return base


def _formatar(d: datetime, formato: str | None) -> str:
    if formato in (None, "o"):
        sufixo = "Z" if d.tzinfo is not None else ""
        return f"{d:%Y-%m-%dT%H:%M:%S}.{d.microsecond:06d}0{sufixo}"
    if formato == "yyyyMM":
        return f"{d.year:04d}{d.month:02d}"
    raise ErroDeExpressao(f"formato não coberto: {formato!r}")


def _menos_meses(d: datetime, meses: int) -> datetime:
    indice = d.year * 12 + d.month - 1 - meses
    ano, mes = divmod(indice, 12)
    dia = min(d.day, calendar.monthrange(ano, mes + 1)[1])  # como o AddMonths do .NET
    return d.replace(year=ano, month=mes + 1, day=dia)


def _inteiro(valor: Any) -> int:
    if not isinstance(valor, int) or isinstance(valor, bool):
        raise ErroDeExpressao(f"esperado inteiro, veio {valor!r}")
    return valor


def _texto(valor: Any) -> str:
    if not isinstance(valor, str):
        raise ErroDeExpressao(f"esperado texto, veio {valor!r}")
    return valor


# --- funções --------------------------------------------------------------------------------


def _if(_: Contexto, condicao: Any, sim: Any, nao: Any) -> Any:
    if not isinstance(condicao, bool):
        raise ErroDeExpressao(f"if com condição não booleana: {condicao!r}")
    if isinstance(sim, list | dict) or isinstance(nao, list | dict):
        raise ErroDeExpressao("o validador do ADF recusa if que devolve array ou objeto")
    return sim if condicao else nao


def _string(_: Contexto, valor: Any) -> str:
    if isinstance(valor, list | dict):
        raise ErroDeExpressao("o validador do ADF recusa string() de array ou objeto")
    if isinstance(valor, bool):
        return "True" if valor else "False"
    return str(valor)


def _range(_: Contexto, inicio: Any, quantos: Any) -> list[int]:
    if _inteiro(quantos) < 1:
        raise ErroDeExpressao(f"range com quantidade {quantos}")
    return list(range(_inteiro(inicio), inicio + quantos))


def _subtrair(_: Contexto, ts: Any, n: Any, unidade: Any, formato: Any = None) -> str:
    if unidade != "Month":
        raise ErroDeExpressao(f"unidade não coberta: {unidade!r}")
    return _formatar(_menos_meses(_data(ts), _inteiro(n)), formato)


def _inicio_do_mes(_: Contexto, ts: Any, formato: Any = None) -> str:
    return _formatar(_data(ts).replace(day=1, hour=0, minute=0, second=0, microsecond=0), formato)


def _de_utc(_: Contexto, ts: Any, fuso: Any, formato: Any = None) -> str:
    if fuso not in FUSOS_WINDOWS:
        raise ErroDeExpressao(f"fuso desconhecido: {fuso!r}")
    d = _data(ts)
    d = d.replace(tzinfo=UTC) if d.tzinfo is None else d
    local = d.astimezone(ZoneInfo(FUSOS_WINDOWS[fuso])).replace(tzinfo=None)
    return _formatar(local, formato)


def _vazio(_: Contexto, valor: Any) -> bool:
    if valor is None:
        return True
    if isinstance(valor, str | list | dict):
        return len(valor) == 0
    raise ErroDeExpressao(f"empty() de {valor!r}")


def _join(_: Contexto, itens: Any, separador: Any) -> str:
    if not isinstance(itens, list):
        raise ErroDeExpressao(f"join() de {itens!r}")
    return _texto(separador).join(_string(_, i) for i in itens)


def _json(_: Contexto, texto: Any) -> Any:
    return json.loads(_texto(texto))


def _uniao(_: Contexto, *listas: Any) -> list[Any]:
    """union() de arrays: os itens de todas, sem repetição, na ordem em que aparecem."""
    if not listas or not all(isinstance(lista, list) for lista in listas):
        raise ErroDeExpressao(f"union() só coberto para arrays: {listas!r}")
    vistos: list[Any] = []
    for lista in listas:
        for item in lista:
            if item not in vistos:
                vistos.append(item)
    return vistos


def _concat(_: Contexto, *partes: Any) -> str:
    return "".join(_texto(p) for p in partes)


FUNCOES: dict[str, Callable[..., Any]] = {
    "concat": _concat,
    "if": _if,
    "empty": _vazio,
    "equals": lambda _, a, b: a == b,
    "range": _range,
    "union": _uniao,
    "add": lambda _, a, b: _inteiro(a) + _inteiro(b),
    "sub": lambda _, a, b: _inteiro(a) - _inteiro(b),
    "string": _string,
    "json": _json,
    "join": _join,
    "subtractFromTime": _subtrair,
    "startOfMonth": _inicio_do_mes,
    "convertFromUtc": _de_utc,
    "utcNow": lambda c, formato=None: _formatar(c.agora.astimezone(UTC), formato),
    "pipeline": lambda c: {"parameters": c.parametros, "RunId": c.run_id},
    "item": lambda c: c.item,
    "dataset": lambda c: c.dataset,
    "variables": lambda c, nome: c.variaveis[_texto(nome)],
    "activity": lambda c, nome: {"output": c.atividades[_texto(nome)]},
    "trigger": lambda c: {"scheduledTime": c.agendado},
}


def _avaliar(no: No, ctx: Contexto) -> Any:
    if no[0] == "lit":
        return no[1]
    if no[0] == "chamar":
        if no[1] not in FUNCOES:
            raise ErroDeExpressao(f"função não coberta pelo avaliador: {no[1]}")
        args: Sequence[Any] = [_avaliar(a, ctx) for a in no[2]]  # todos, antes da chamada
        try:
            return FUNCOES[no[1]](ctx, *args)
        except (KeyError, TypeError, ValueError) as erro:
            raise ErroDeExpressao(f"{no[1]}: {erro}") from erro
    alvo = _avaliar(no[1], ctx)
    chave = no[2] if no[0] == "prop" else _avaliar(no[2], ctx)
    try:
        return alvo[chave]
    except (KeyError, IndexError, TypeError) as erro:
        raise ErroDeExpressao(f"{chave!r} não existe em {alvo!r}") from erro


def avaliar(valor: Any, ctx: Contexto) -> Any:
    """Valor de uma propriedade do JSON: expressão ({"type": "Expression"} ou texto com '@')
    avaliada; qualquer outro valor devolvido como está."""
    if isinstance(valor, dict) and valor.get("type") == "Expression":
        return _avaliar(ler(valor["value"]), ctx)
    if isinstance(valor, str) and valor.startswith("@"):
        return _avaliar(ler(valor), ctx)
    return valor
