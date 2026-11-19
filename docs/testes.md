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
- o dia da atualização semanal da CVM (observado uma vez: sábado, 14/11/2026);
- se o `ETag` do servidor da CVM fica igual quando o arquivo é regravado sem mudança (necessário
  para a otimização futura do D3).
