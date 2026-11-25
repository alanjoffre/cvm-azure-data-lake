# Como se testa

O ADF não tem emulador, e o Azurite não implementa o namespace hierárquico do ADLS Gen2 (D13).
Por isso, cada parte é validada pela ferramenta que a confere sem a nuvem, e o que só a Azure
confirma fica listado à parte.

## Na máquina e no CI

| Parte | Como se testa | Prova de quê |
|---|---|---|
| Carga, janela de reprocessamento, republicação, contagem e hash | pytest com dados sintéticos no formato da CVM, e a prova completa com os arquivos reais baixados da CVM | a lógica da tese: substitui o mês sem duplicar e sem perder |
| Bronze, staging, prata e ouro | pastas locais com o mesmo layout do ADLS. O código só troca a raiz (pasta local ou o endpoint dfs da conta). Ouro em DuckDB | camadas, partições e agregados |
| Function na Azure | [`tests/test_funcao.py`](../tests/test_funcao.py) (21 testes): o adaptador do ADLS sobre um serviço falso com a interface do SDK (a gravação num nome temporário e o rename por cima, sem truncar o arquivo vigente), a prova de ponta a ponta por ele, o controle que o lake recusa virando `ok: false` sem perder o manifesto anterior, o contrato HTTP (200 com `ok: false`, 400 para corpo inválido), o `HEAD` que falha sem derrubar a carga e as rotas do runtime contra os `functionName` do JSON | a Function chama o mesmo código da prova local e responde o que a guarda do pipeline lê |
| Pacote e scripts de deploy | [`tests/test_deploy.py`](../tests/test_deploy.py) (19 testes): o zip com o layout do Python, reprodutível e importável depois de extraído; os parâmetros do ARM a partir das saídas do Terraform; o pré e o pós-script com os argumentos que o script gerado declara (parar os dois gatilhos, os parâmetros do ambiente); `funcao.sh` e `terraform.sh` com `az` e `terraform` falsos (a chave nunca na linha de comando nem no log, a segunda execução não regrava) | o deploy pede à CLI o que a doc manda, sem vazar o segredo |
| Terraform | [`infra/validar.sh`](../infra/validar.sh): `terraform fmt -check`, `terraform validate`, tflint (com as regras do azurerm), Checkov e `terraform test` com `mock_provider` (16 testes, sem conta; o da raiz roda com dev e com prod) | infraestrutura coerente e com as regras de segurança do desenho |
| ADF (JSON) | [`ci/adf/validar.sh`](../ci/adf/validar.sh): `@microsoft/azure-data-factory-utilities` (validate + export do ARM) e [`tests/test_adf.py`](../tests/test_adf.py) (31 testes: referências, `dependsOn` que o validador não confere, caminhos iguais aos do código, contrato com a Function, meses dos gatilhos contra a janela, parâmetros do ARM) | o JSON é válido (esquema e referências), gera o ARM e faz o que o código local faz; não prova a execução ([`adf.md`](adf.md#validar)) |
| ARM gerado | [`ci/adf/armttk.sh`](../ci/adf/armttk.sh): arm-ttk fixado pelo commit da release, com PowerShell 7, sobre o ARM que o `ci/adf/validar.sh` exporta. Quatro testes pulados, com o motivo no script: reprovam o que o gerador da Microsoft escreve (`$schema` com `http://`, que dispara também a regra de endereço fixo; propriedades vazias; todo `dependsOn` como `concat(variables('factoryId'), …)`). O que as regras de endereço e de `dependsOn` cobririam, o teste do ARM em `tests/test_adf.py` confere | boas práticas do template |
| Azure Pipelines (YAML) | `check-jsonschema` com o esquema publicado do Azure Pipelines, nos dois arquivos; os scripts das etapas rodam nos testes e no shellcheck | a estrutura do arquivo e os nomes das tasks e das entradas; variáveis, templates e checks só se resolvem no Azure DevOps |
| Tudo | [GitHub Actions](../.github/workflows/ci.yml) a cada push e pull request, sem credencial: Python, Terraform, ADF com arm-ttk, YAML e shellcheck | o selo público do CI (o deploy fica no Azure Pipelines) |

## O que a prova da tese cobra

Para cada mês carregado (na staging, antes da troca, e na prata, depois):
1. a contagem de linhas da bronze (CSV) é igual à do Parquet;
2. o SHA-256 canônico (D7) da bronze é igual ao do Parquet;
3. a chave `(CNPJ_FUNDO_CLASSE, ID_SUBCLASSE, DT_COMPTC)` é única;
4. toda `DT_COMPTC` cai no mês da partição (a chave só é única dentro dela; conferido também
   nos três meses reais baixados em 24/11/2026: nenhuma linha fora do mês);
5. o ouro soma as mesmas linhas da prata.

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

## O que os testes do Terraform cobram

O provider da Azure é simulado: o teste confere o que o plano pediria, não o que a Azure aceita.
- Lake sem chave compartilhada, sem blob anônimo, TLS 1.2, namespace hierárquico; um container
  privado por camada; ciclo de vida de 90 dias na bronze e de 7 na staging.
- ADF com identidade do sistema e sem integração Git (D9).
- Function no Flex Consumption (FC1), Python 3.12, com conta própria para o host, também sem
  chave, e o pacote lido pela identidade.
- Key Vault por RBAC; nenhum segredo criado pelo Terraform (D11).
- As 6 atribuições de papel, cada uma para a identidade certa, todas para `ServicePrincipal`, e
  no lake só `Storage Blob Data Contributor`.
- A condição do RBAC Administrator no bootstrap: os 5 papéis da lista nas duas metades (criar e
  remover), só `ServicePrincipal`, nenhum papel de administração; `Contributor` e RBAC
  Administrator só no resource group do ambiente.
- Orçamento começando no primeiro dia do mês, com aviso pelo gasto real e pela previsão; alerta
  na primeira execução de pipeline com falha.
- Entradas recusadas: ambiente fora de dev e prod, lista de e-mails vazia.

Os testes foram conferidos pelo avesso: ligar a chave compartilhada do lake ou dar
`Storage Blob Data Owner` ao ADF no lake faz o teste falhar, e um papel fora da lista quebra o
plano.

As exceções do Checkov e do tflint estão no próprio recurso, cada uma com o motivo e a decisão
que a justifica (quase todas pela D12: endpoint privado, VNet gerenciada e Log Analytics ficam fora
por custo).

## O substituto local do ADF

[`adf_local.py`](../src/cvm_lake/adf_local.py) faz o papel das atividades que movem dado (Copy,
Delete) e dos dois pipelines, na mesma ordem. O código da Function (`cvm_lake.funcao`) é o
mesmo que vai para a Azure. Diferenças declaradas:
- o Copy ZIP → Parquet é feito pelo leitor de CSV do DuckDB, já com os tipos do dicionário. O
  tipo físico que o Copy do ADF grava só se confirma na Azure. Os testes cobrem as variantes
  "tudo como texto", "cota como double" e a dos tipos do mapeamento do JSON (texto, `Decimal`
  e `Int64`). A do double muda o hash quando a cota tem mais dígitos do que o double guarda, o
  que acontece em 1.923 linhas de setembro
  ([`dado.md`](dado.md#por-que-o-tipo-da-cota-importa));
- o leitor tipado aceita, sem erro, um decimal com casas a mais. Quem barra é o validador, pelo
  padrão do dicionário (teste `test_um_mes_ruim_nao_impede_os_outros`);
- o ForEach roda os meses em sequência. No ADF são lotes em paralelo, e cada mês só escreve nos
  próprios caminhos;
- os tempos do relatório são desta máquina. A folga contra os 230 s que a Function tem para
  responder por HTTP (menos de 3 s no pior mês, medido aqui) se confirma na execução na Azure.

Limite: a tese é provada pela execução local da mesma lógica, nas mesmas pastas e com a mesma
janela. A equivalência com o ADF executando só se confirma implantando.

## Só na Azure

O que nenhum teste local confirma (o roteiro para conferir está em [`runbook.md`](runbook.md)):
- O ADF executando: expressões, Copy de ZIP para Parquet com `preserveZipFileNameAsFolder = false`,
  ForEach com Execute Pipeline, `concurrency: 1` e os gatilhos (inclusive o nome do fuso de
  Brasília no gatilho). As expressões são avaliadas nos testes por um avaliador próprio
  ([`adf_expressao.py`](../tests/adf_expressao.py)), não pelo motor do ADF; ele avalia os dois
  ramos do `if`, como o ADF, mas a equivalência só se confirma executando. Inclui o `union` da
  lista de meses com ela mesma, tirando a repetição dentro da própria lista.
- Os tipos físicos que o Copy grava no Parquet: precisão e escala do `Decimal`, que a
  documentação não descreve, e o `Int64`. Se não forem os do dicionário, a reconciliação acusa
  pelo hash e vale o plano B (D2b).
- A atividade Azure Function: o `output.ok` lido pela guarda, o limite de 230 s no Flex
  Consumption e o que a atividade faz com cada status HTTP de erro.
- O Delete de uma pasta inteira no ADLS Gen2 e o Get Metadata `exists` de uma partição que não
  existe.
- A entrada HTTP da Function fazendo o `HEAD` na CVM e entregando `ETag` e `Last-Modified`.
- Permissões reais: identidade gerenciada no lake e no Key Vault, chave compartilhada desligada no
  lake, e o host da Function (Flex Consumption) acessando o próprio armazenamento por identidade.
- O `terraform apply` com a chave compartilhada desligada nas contas: o provider configurado com
  `storage_use_azuread` e sem acesso ao plano de dados (`data_plane_available = false`), os
  containers criados pelo plano de controle.
- O contorno do `AzureWebJobsStorage`: o provider 4.x grava essa configuração como string de
  conexão mesmo com identidade (issues 29693 e 33211 do `terraform-provider-azurerm`). O módulo
  a deixa vazia e usa `AzureWebJobsStorage__accountName` e `__credential`; conferir que o host
  sobe e que o plano seguinte fica sem diferença.
- A condição do RBAC Administrator aceita pela Azure e recusando, de fato, um papel fora da lista
  e uma atribuição a usuário.
- O orçamento criado com a data de início do mês e o aviso chegando por e-mail.
- A identidade do pipeline, só com papéis nos resource groups, lendo a assinatura
  (`data.azurerm_subscription`) e passando na verificação da service connection.
- O SDK do ADLS, dentro da Function, lendo a staging e a prata e gravando o ouro e o controle
  com a identidade gerenciada (o rename por cima do arquivo vigente); o pacote subindo pelo
  build remoto do Flex Consumption.
- O campo `functionKeys` da saída de `az functionapp keys list`, que o `funcao.sh` lê (a doc da
  CLI não descreve o formato), e o papel do pipeline no cofre valendo a tempo (o script tenta de
  novo por alguns minutos).
- O Azure Pipelines com agente próprio, federação de identidade (o provider e o backend renovando
  o token pela service connection, com o ID dela em `AZURESUBSCRIPTION_SERVICE_CONNECTION_ID`,
  que a página da task não documenta), aprovação no environment antes do apply e deploy de dev
  para prod; o build de PR de fork desligado; o pré-script parando os dois gatilhos.
- O alerta de falha chegando por e-mail.
- O custo: contagem de execuções de atividade cobradas, preço do Flex Consumption e do Key Vault.

Premissas da fonte, conferidas ao longo da operação:
- o dia da atualização semanal da CVM (observado duas vezes: sábados, 14/11/2026 e 21/11/2026,
  quando setembro ganhou 95 linhas, ver
  [`dado.md`](dado.md#a-fonte-reescrevendo-o-passado-fato-observado));
- se o `ETag` do servidor da CVM fica igual quando o arquivo é regravado sem mudança (necessário
  para a otimização futura da D3);
- se o arquivo do mês novo já existe no primeiro dia útil, às 10:00 (se não existir, o Copy de M
  falha e o pai termina com falha, ver D3).
