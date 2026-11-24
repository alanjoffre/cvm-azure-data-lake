# Como se testa

O ADF não tem emulador, e o Azurite não implementa o namespace hierárquico do ADLS Gen2 (D13).
Por isso cada parte é validada pela ferramenta que a confere sem a nuvem, e o que só a Azure
confirma fica listado à parte.

## Na máquina e no CI

| Parte | Como se testa | Prova de quê |
|---|---|---|
| Carga, janela de reprocessamento, republicação, contagem e hash | pytest com dados sintéticos no formato da CVM, e a prova completa com os arquivos reais baixados da CVM | a lógica da tese: substitui o mês sem duplicar e sem perder |
| Bronze, staging, prata e ouro | pastas locais com o mesmo layout do ADLS. O código só troca a raiz (pasta local ou `abfss://`). Ouro em DuckDB | camadas, partições e agregados |
| Terraform | `terraform fmt -check`, `terraform validate`, tflint, Checkov e `terraform test` com `mock_provider` (plano sem conta) | infraestrutura coerente e com as regras de segurança do desenho |
| ADF (JSON) | `@microsoft/azure-data-factory-utilities` (validate + export do ARM) e testes próprios de referência entre recursos e parâmetros | o JSON é válido (esquema e referências) e gera o ARM; não prova a execução |
| ARM gerado | arm-ttk | boas práticas do template |
| Azure Pipelines (YAML) | validação contra o esquema publicado; os scripts das etapas rodam no CI do GitHub | a estrutura do arquivo; variáveis e templates só se resolvem no Azure DevOps |
| Tudo | GitHub Actions a cada push | selo verde |

## O que a prova da tese cobra

Para cada mês carregado (na staging, antes da troca, e na prata, depois):
1. a contagem de linhas da bronze (CSV) é igual à do Parquet;
2. o SHA-256 canônico (D7) da bronze é igual ao do Parquet;
3. a chave `(CNPJ_FUNDO_CLASSE, ID_SUBCLASSE, DT_COMPTC)` é única;
4. o ouro soma as mesmas linhas da prata.

Na republicação simulada (T0 → T1 do mesmo mês):
1. a prata do mês é igual a T1;
2. os outros meses mantêm o hash anterior;
3. reexecutar não muda nenhum hash;
4. a bronze guarda as duas versões.

Uma carga que não passa na validação não pode trocar a partição: o teste injeta uma divergência
na staging e cobra que a prata continue com a versão anterior.

O resultado com os arquivos reais está em [`reports/prova.md`](../reports/prova.md), gerado
por `uv run cvm-lake prova` (código em [`prova_completa.py`](../src/cvm_lake/prova_completa.py)).
A mesma rotina roda no pytest sobre dado sintético.

Além disso, o hash canônico tem uma segunda implementação, em Python puro
([`referencia.py`](../src/cvm_lake/referencia.py)), e a prova cobra que ela e o DuckDB cheguem
ao mesmo SHA-256 em cada mês. A forma canônica é uma especificação, não um detalhe do DuckDB. Foi
essa comparação que achou um defeito na primeira versão da referência: `str(Decimal)` escreve
a cota zero como `0E-12`.

## O substituto local do ADF

[`adf_local.py`](../src/cvm_lake/adf_local.py) faz o papel das atividades que movem dado (Copy,
Delete) e dos dois pipelines, na mesma ordem. O código da Function (`cvm_lake.funcao`) é o
mesmo que vai para a Azure. Diferenças declaradas:
- o Copy ZIP → Parquet é feito pelo leitor de CSV do DuckDB, já com os tipos do dicionário. O
  tipo físico que o Copy do ADF grava só se confirma na Azure. Os testes cobrem as variantes
  "tudo como texto" e "cota como double". Esta última muda o hash quando a cota tem mais
  dígitos do que o double guarda, o que acontece em 1.923 linhas de setembro
  ([`dado.md`](dado.md#por-que-o-tipo-da-cota-importa));
- o leitor tipado aceita, sem erro, um decimal com casas a mais. Quem barra é o validador, pelo
  padrão do dicionário (teste `test_um_mes_ruim_nao_impede_os_outros`);
- o ForEach roda os meses em sequência. No ADF são lotes em paralelo, e cada mês só escreve nos
  próprios caminhos;
- os tempos do relatório são desta máquina. A folga contra os 230 s que o ADF espera pela
  resposta da Function (menos de 3 s no pior mês, medido aqui) se confirma na F4.

Limite: a tese é provada pela execução local da mesma lógica, nas mesmas pastas e com a mesma
janela. A equivalência com o ADF executando só se confirma implantando.

## Só na Azure

O que nenhum teste local confirma, e o roteiro para conferir está em [`runbook.md`](runbook.md):
- O ADF executando: expressões, Copy de ZIP para Parquet com `preserveZipFileNameAsFolder = false`,
  ForEach com Execute Pipeline, `concurrency: 1` e os gatilhos (inclusive o nome do fuso de
  Brasília no gatilho).
- Os tipos físicos que o Copy grava no Parquet (precisão e escala do decimal, `DATE` ou
  timestamp). Se não forem os do dicionário, vale o plano B (D2b).
- A atividade Azure Function falhando quando a Function responde erro.
- Permissões reais: identidade gerenciada no lake e no Key Vault, chave compartilhada desligada no
  lake, e o host da Function (Flex Consumption) acessando o próprio armazenamento por identidade.
- A extensão `azure` do DuckDB, dentro da Function, lendo e gravando em `abfss://` com a
  identidade gerenciada.
- O Azure Pipelines com agente próprio, federação de identidade, aprovação e deploy de dev para
  prod; o build de PR de fork desligado.
- O alerta de falha chegando por e-mail.
- O custo: contagem de execuções de atividade cobradas, preço do Flex Consumption e do Key Vault.

Premissas da fonte, conferidas ao longo da operação:
- o dia da atualização semanal da CVM (observado duas vezes: sábados, 14/11/2026 e 21/11/2026,
  quando setembro ganhou 95 linhas, ver [`dado.md`](dado.md#a-fonte-reescrevendo-o-passado-fato-observado));
- se o `ETag` do servidor da CVM fica igual quando o arquivo é regravado sem mudança (necessário
  para a otimização futura do D3).
