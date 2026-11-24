# O Data Factory em JSON

Os recursos do ADF ficam em [`adf/`](../adf/), no formato da integração Git do ADF Studio: uma
pasta por tipo de recurso e um arquivo por recurso. A fábrica não tem integração Git ligada
(D9). O JSON deste repositório é a fonte, e chega ao ADF pelo ARM que o build exporta.

```
adf/
├── factory/adf-cvm-lake.json         a fábrica (identidade do sistema); o ARM não a recria
├── linkedService/                    CVM (HTTP), lake (ADLS Gen2), Key Vault e Function
├── dataset/                          o ZIP na CVM; arquivo, pasta, CSV do ZIP e Parquet no lake
├── pipeline/pl_inf_diario.json       o pai: escolhe os meses e chama o filho por mês
├── pipeline/pl_inf_diario_mes.json   o filho: os 7 passos de um mês
└── trigger/                          diário e semanal
```

Não validado na Azure: validado na máquina com `ci/adf/validar.sh` (ver [Validar](#validar)). Cada
atividade tem no próprio JSON uma `description` com o motivo dela.

## Pipeline pai: `pl_inf_diario`

| Parâmetro | Tipo | Padrão | Uso |
|---|---|---|---|
| `de`, `ate` | int | 0, 1 | a janela: de M-`de` a M-`ate` (D3) |
| `referencia` | string | vazio | a data que define M; vazio = agora. Os gatilhos passam `@trigger().scheduledTime` |
| `competencias` | array | vazio | lista explícita de AAAAMM (carga inicial, reprocessamento); quando preenchida, `de` e `ate` são ignorados |

1. `escolher_meses` (IfCondition): sem lista, a variável `meses` recebe
   `range(de, ate - de + 1)`, os deslocamentos de M. Com lista, recebe a própria lista. A escolha
   passa por uma variável porque o `if` da linguagem de expressões não devolve array: o
   validador recusa `if(Bool, Array, Array)` e também `string()` de array.
2. `para_cada_competencia` (ForEach, lotes de 4 em paralelo) chama o filho com:
   - `competencia`: o deslocamento aplicado ao primeiro dia do mês de `referencia` convertida
     para o horário de Brasília (`convertFromUtc` com `E. South America Standard Time`,
     `startOfMonth`, `subtractFromTime` em meses, formato `yyyyMM`). Numa execução manual às
     23:30 do dia 30, que em UTC já é dia 1º, M continua sendo o mês de Brasília. Quando o item já
     é um AAAAMM da lista, vale o próprio item. A expressão funciona nos dois modos mesmo que o
     ADF avalie os dois ramos do `if`: um `if` interno entrega 0 ao `subtractFromTime` quando o
     item é da lista;
   - `carga`: o `RunId` do pai, o mesmo para todos os meses da execução.

`concurrency: 1`: uma execução que chega com outra em andamento espera na fila. Um mês que
falha não interrompe os outros, e o pai termina como falho.

## Pipeline filho: `pl_inf_diario_mes`

Parâmetros `competencia` (AAAAMM) e `carga`. Pode ser executado sozinho para um mês. Os caminhos
são os de [`lake.py`](../src/cvm_lake/lake.py) (seção 4 da [arquitetura](arquitetura.md)). Os
testes calculam os caminhos a partir das expressões do JSON e comparam com esse arquivo.

| # | Atividade | Tipo | O que faz | Retentativa |
|---|---|---|---|---|
| 1 | `copiar_origem_para_bronze` | Copy (binário) | HTTP da CVM → `bronze/.../competencia=AAAAMM/carga=<RunId>/inf_diario_fi_AAAAMM.zip`, sem descompactar (D5) | 2 |
| 2 | `copiar_zip_para_staging` | Copy | o CSV de dentro do ZIP da bronze → `staging/.../carga=<RunId>/competencia=AAAAMM/inf_diario_fi_AAAAMM.parquet` | 2 |
| 3 | `validar_staging` | Azure Function | `validar`: bronze × staging, contagem, SHA-256 canônico e chave única (D7) | 0 |
|   | `validacao_ok` | IfCondition → Fail | `ok = false` derruba a carga com o código `CVM_VALIDACAO`; a prata não foi tocada | — |
|   | `prata_existe` | Get Metadata (`exists`) | na primeira carga do mês a partição não existe; com `exists`, a atividade responde `false` em vez de falhar | 2 |
| 4 | `apagar_prata_se_existe` | IfCondition → Delete | a partição do mês sai inteira (D6) | 2 |
| 5 | `copiar_staging_para_prata` | Copy (binário) | o Parquet validado vai para `prata/.../competencia=AAAAMM/`, sem reprocessar | 2 |
| 6 | `reconciliar_prata` | Azure Function | `reconciliar`: bronze × prata, refaz o ouro do mês, confere o ouro e grava o manifesto | 0 |
|   | `reconciliacao_ok` | IfCondition → Fail | `ok = false` derruba a carga com `CVM_RECONCILIACAO`; o manifesto não é gravado | — |
| 7 | `apagar_staging` | Delete | a staging da carga sai. Se a carga falhou antes, ela fica para diagnóstico, e o ciclo de vida a apaga em 7 dias | 2 |

Cada atividade depende da anterior só em caso de sucesso. A primeira falha para o mês, e nada
depois dela roda. As retentativas ficam nas atividades em que repetir é seguro, porque a mesma
carga regrava os mesmos caminhos. As chamadas à Function não têm retentativa: a resposta é
determinística, e repetir não muda o resultado.

### Passo 2: ZIP → Parquet sem Data Flow

A atividade Copy lê o CSV direto do ZIP. O formato Binary só copia para Binary, então a origem é
um dataset DelimitedText com compressão `ZipDeflate`:
- `;` como separador, primeira linha como cabeçalho, `ISO-8859-1` (o arquivo é ASCII, medido em
  [`dado.md`](dado.md); o latin-1 contém o ASCII e é o encoding do dicionário);
- `compressionProperties` com `preserveZipFileNameAsFolder: false`: sem isso, o Copy cria uma
  subpasta com o nome do ZIP;
- destino com nome fixo (`inf_diario_fi_AAAAMM.parquet`): sem ele, o Copy repetiria o nome do
  CSV, com a extensão `.csv`;
- mapeamento explícito das 10 colunas, na ordem do dicionário, com `typeConversion` ligado e
  `allowDataTruncation: false`: um valor que não cabe no tipo derruba a cópia em vez de ser
  arredondado.

| Coluna | Tipo no Copy | Por quê |
|---|---|---|
| `TP_FUNDO_CLASSE`, `CNPJ_FUNDO_CLASSE`, `ID_SUBCLASSE` | String | texto |
| `DT_COMPTC` | String (ISO) | o Copy não tem tipo de data sem hora; `Datetime` viraria `INT96` no Parquet |
| `VL_TOTAL`, `VL_QUOTA`, `VL_PATRIM_LIQ`, `CAPTC_DIA`, `RESG_DIA` | Decimal | decimal exato; o double perde a cota longa (D2b) |
| `NR_COTST` | Int64 | `int(10)` no dicionário: até 9.999.999.999, além dos 32 bits |

A validação do passo 3 tipa o Parquet pelo dicionário, qualquer que seja o tipo físico gravado,
e compara o hash com o do CSV da bronze. Um teste grava a staging com os tipos desta tabela e
passa pelo `validar` real. A precisão e a escala com que o Copy grava o Decimal no Parquet não
estão documentadas: se não forem as do dicionário, a reconciliação acusa pelo hash, e vale o
plano B (D2b).

### Passos 3 e 6: o contrato com a Function

O ADF chama a Function por `POST` com o corpo
`{"competencia": "AAAAMM", "carga": "<RunId>"}`, os parâmetros de `validar` e `reconciliar` em
[`prova.py`](../src/cvm_lake/funcao/prova.py), fora o armazenamento e o lake. A Function responde
um objeto JSON (a atividade exige objeto, não lista) com `etapa`, `ok`, `falhas` e os resumos.
O limite de espera da atividade é de 230 s.

A falha da prova não depende de como a atividade trata o status HTTP, que a documentação não
descreve: a Function responde `ok: false`, e a atividade Fail seguinte derruba a carga com as
falhas na mensagem. Se a própria Function cair (exceção, tempo esgotado, status de erro), ou a
atividade falha, ou a resposta não traz `ok: true` e a guarda falha. Nos dois casos, a carga
para.

A entrada HTTP da Function, que ainda não está no repositório, fará um `HEAD` na URL do mês e
entregará `ETag` e `Last-Modified` ao `reconciliar` (parâmetro `origem`), que os grava no
manifesto. O ADF não lê cabeçalho HTTP sem código: o Copy não expõe os cabeçalhos da resposta,
e a atividade Web não faz `HEAD` (D3).

## Gatilhos

| Gatilho | Agenda (`E. South America Standard Time`) | `de`, `ate` | Meses |
|---|---|---|---|
| `tr_diario` | semanal, seg a sáb, 10:00 | 0, 1 | M e M-1 |
| `tr_semanal` | semanal, domingo, 10:00 | 2, 11 | M-2 a M-11 |

Os dois passam `referencia = @trigger().scheduledTime`. Com fuso fora de UTC, o `startTime` vai
sem o `Z`. O `runtimeState` no JSON é `Started`. No deploy, o script da Microsoft
(`PrePostDeploymentScript.ps1`, gerado pelo export) para antes do ARM os gatilhos que mudaram e
liga, depois do ARM, os que estão `Started` no template. Os testes conferem os meses de cada
gatilho contra [`janela.py`](../src/cvm_lake/janela.py) em ~270 datas, inclusive as viradas de
ano.

## Parâmetros do ARM

O export gera um parâmetro para cada endereço do ambiente. No JSON, os endereços do lake, do
cofre e da Function usam o domínio `definido-no-deploy.invalid`, que nunca resolve (RFC 2606):
sem o parâmetro, nada conecta.

| Parâmetro do ARM | Valor no deploy (saída do Terraform) |
|---|---|
| `factoryName` | `adf_nome` |
| `ls_adls_lake_properties_typeProperties_url` | `lake_dfs_endpoint` |
| `ls_kv_properties_typeProperties_baseUrl` | `key_vault_uri` |
| `ls_funcao_properties_typeProperties_functionAppUrl` | `funcao_host` |

O ARM não tem parâmetro secreto. A chave da Function fica no Key Vault, no segredo
`funcao-chave-adf`, gravado pela etapa de deploy (D11). O lake e o cofre são acessados pela
identidade gerenciada da fábrica, sem credencial no linked service. O ARM traz os recursos de
dentro da fábrica, não a fábrica: ela e a identidade são do Terraform (D9).

## Validar

Precisa de Node 20 ou 22 e [uv](https://docs.astral.sh/uv/).

```bash
ci/adf/validar.sh      # validate, export do ARM para saida/arm e os testes do JSON e do ARM
```

- **`validate` e `export`** pelo [`@microsoft/azure-data-factory-utilities`](https://www.npmjs.com/package/@microsoft/azure-data-factory-utilities),
  versão fixa em `ci/adf/package-lock.json`. O pacote baixa a cada execução o validador que o
  ADF Studio usa, que não tem versão. O script registra o SHA-256 dele, e uma regra nova da
  Microsoft pode reprovar um JSON que antes passava. O ID da fábrica é só texto: nada conecta à
  Azure.
- **O que o validador pega:** dataset, linked service ou parâmetro inexistente e erro de tipo
  em expressão (conferido quebrando cada um). **O que ele não pega:** um `dependsOn` que aponta
  para uma atividade que não existe.
- **[`tests/test_adf.py`](../tests/test_adf.py)** cobre o resto, com um avaliador mínimo da
  linguagem de expressões ([`tests/adf_expressao.py`](../tests/adf_expressao.py)) que avalia
  todos os argumentos, inclusive os dois ramos do `if`. Ele confere: referências e parâmetros, a
  ordem dos passos, caminhos iguais aos de `lake.py`, URL igual à de `origem.py`, meses iguais
  aos de `janela.py`, corpo da Function igual aos parâmetros de `prova.py`, a guarda Fail depois
  de cada Function, as retentativas, os tipos do Copy aceitos pelo `validar`, nenhum segredo nem
  ID de assinatura e os parâmetros do ARM.

O que só a execução na Azure confirma está em [`testes.md`](testes.md#só-na-azure).
