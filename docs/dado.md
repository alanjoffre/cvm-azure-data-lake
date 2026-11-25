# O dado: Informe Diário de fundos da CVM

Medido nos arquivos de setembro, outubro e novembro de 2026, baixados em 24/11/2026 pelo
`cvm-lake baixar`. Os números por mês vêm do [relatório da prova](../reports/prova.md).

## Origem

- Um ZIP por mês: `https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_AAAAMM.zip`.
- Dicionário de dados:
  `https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/META/meta_inf_diario_fi.txt`, em latin-1.
- Licença: Open Database License (ODbL). O repositório não contém nem redistribui o dado.
- O servidor devolve `ETag` e `Last-Modified`, que o manifesto de cada mês guarda.

## Formato físico

| Item | Medido |
|---|---|
| ZIP | exatamente um CSV, com o nome `inf_diario_fi_AAAAMM.csv` |
| Separador | `;` |
| Fim de linha | CRLF em todas as linhas (nenhum LF solto) |
| Encoding | só ASCII (nenhum byte acima de 127), sem BOM |
| Aspas | nenhuma |
| Cabeçalho | na primeira linha, com os nomes do dicionário |
| Tamanho | ~54 MB de CSV e ~11,6 MB de ZIP por mês fechado; ~539 mil linhas |

A leitura não depende de detecção automática: o cabeçalho é conferido contra o dicionário, e o
tipo de cada coluna vem do dicionário ([`esquema.py`](../src/cvm_lake/esquema.py)).

## Colunas

| Coluna | Tipo no dicionário | Observado |
|---|---|---|
| `TP_FUNDO_CLASSE` | varchar(20) | 3 valores: `CLASSES - FIF`, `CLASSE FIF/FAPI`, `FI` |
| `CNPJ_FUNDO_CLASSE` | varchar(18) | formato `00.000.000/0000-00` |
| `ID_SUBCLASSE` | varchar(15) | vazio em 96% das linhas: o vazio é um valor da chave, não ausência |
| `DT_COMPTC` | date | só dias de semana (22 em setembro e outubro); nos feriados do período (07/09, 12/10 e 02/11), no máximo 13 linhas, contra ~24 mil num dia comum; 20/11 (feriado nacional) não aparece |
| `VL_TOTAL` | numeric(17,2) | negativos em 213 a 277 linhas por mês; em outubro chega a 15 dígitos inteiros, o limite exato do tipo |
| `VL_QUOTA` | numeric(27,12) | negativos em 380 a 622 linhas por mês, e zeros; até 21 dígitos significativos |
| `VL_PATRIM_LIQ` | numeric(17,2) | negativos em 402 a 601 linhas por mês |
| `CAPTC_DIA` | numeric(17,2) | nunca negativo |
| `RESG_DIA` | numeric(17,2) | nunca negativo |
| `NR_COTST` | int(10) | máximo observado: 816.736 |

Os decimais chegam sempre com a escala exata do dicionário (2 ou 12 casas). Nenhuma coluna além
de `ID_SUBCLASSE` tem vazio.

**Chave:** `(CNPJ_FUNDO_CLASSE, ID_SUBCLASSE, DT_COMPTC)`, sem nenhuma duplicata nos três meses.

## Por que o tipo da cota importa

A cota tem até 21 dígitos significativos (ex.: `112439113.205550566316`, out./2026), e um `double`
guarda uns 15. Se o Copy gravasse `VL_QUOTA` como `double` no Parquet, mudariam **1.923** cotas em
setembro, **1.866** em outubro e **1.077** em novembro. O PL, com 2 casas, não perde nada. A
reconciliação acusa essa perda pelo hash ([D2b](decisoes.md#d2b-plano-b-para-os-tipos-da-prata)), e
o teste `test_cota_longa_gravada_como_double_muda_o_hash` fixa o caso.

## A fonte reescrevendo o passado (fato observado)

Setembro de 2026 tinha **538.663** linhas em 19/11/2026 e **538.758** em 24/11/2026: a CVM
regravou o mês com 95 linhas a mais. O `Last-Modified` do arquivo é sábado, 21/11/2026, 09:03
GMT. Setembro era M-2 naquele dia, a faixa que muda uma vez por semana. É a segunda atualização
semanal observada num sábado (a primeira, 14/11/2026), a premissa da
[D3](decisoes.md#d3-janela-de-reprocessamento-no-lugar-de-watermark-com-dois-gatilhos).

Uma carga que não recarregasse setembro teria ficado com 95 linhas a menos nesse mês, sem nenhum
erro: é o problema que este projeto resolve.
