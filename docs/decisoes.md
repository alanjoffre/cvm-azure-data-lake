# Registro de decisões

Cada decisão de desenho do case, com o motivo e o que fica de fora. Formato curto de ADR
(*Architecture Decision Record*): contexto, decisão, consequência. O desenho completo está em
[`arquitetura.md`](arquitetura.md).

## D1. Dado público real, baixado na execução e nunca versionado

- **Contexto:** a tese (reprocessar o que a fonte republica) só tem graça com uma fonte que de
  fato republica. O Informe Diário da CVM republica os últimos 12 meses por contrato, tem ~540
  mil linhas por mês e é aberto. A licença é a ODbL, que exige atribuição e obriga quem
  redistribui uma base derivada a usar a mesma licença (*share-alike*).
- **Decisão:** o código baixa os arquivos da CVM na hora de rodar, e nenhum dado da CVM entra no
  repositório (`dados/` e `saida/` estão no `.gitignore`). Os testes de unidade usam dados
  sintéticos no formato exato da CVM. A atribuição está no README.
- **Consequência:** o repositório fica sob MIT, sem conflito de licença. Quem roda a prova
  completa precisa de internet. Se a CVM mudar o layout, a prova quebra de forma visível, e isso
  é o comportamento desejado.

## D2. ADF orquestra e move; a lógica fica numa Azure Function com DuckDB

- **Contexto:** o ADF faz bem buscar, descompactar, converter e gravar (atividade Copy), e cobra
  por execução de atividade. A validação, o ouro e a prova (hash canônico, contagem por chave)
  exigem lógica. No ADF, as opções são Data Flow (cluster Spark gerenciado, cobrado por tempo de
  uso) ou chamar código externo.
- **Decisão:** o ADF faz as cópias (origem → bronze → staging → prata). Uma Azure Function em
  Python, no plano Flex Consumption (o plano de consumo atual), faz a validação, o ouro e a
  reconciliação com DuckDB, e o ADF a chama pela atividade Azure Function. Com ~540 mil linhas por
  mês, o DuckDB resolve em memória em segundos. A Function lê o ZIP da bronze com o `zipfile` do
  Python (11 MB cabem em memória) e o Parquet pela extensão `azure` do DuckDB, com a identidade
  gerenciada.
- **Consequência:** nenhum cluster ligado e uma lógica testável com pytest, na máquina. O mesmo
  módulo Python roda localmente (pastas) e na Function (`abfss://`). A atividade Azure Function do
  ADF espera a resposta em até 230 segundos e exige um objeto JSON como resposta: a Function
  devolve sempre `ok`, contagens e hashes, e o tempo medido na prova local mostra a folga.
- **Armazenamento do host:** o Flex Consumption permite que o host da Function acesse a própria
  conta de armazenamento por identidade gerenciada. Essa conta é separada da do lake, que fica
  com a chave compartilhada desligada.
- **Fora:** Data Flow, Databricks e Synapse. Seriam a escolha com volume de dezenas de GB por
  carga, o que não é o caso aqui.

### D2b. Plano B para os tipos da prata

- **Contexto:** o mapeamento de tipos da atividade Copy não expõe precisão e escala do decimal, e
  o tipo de data que ela grava no Parquet só se confirma executando.
- **Decisão:** se o Copy não gravar os tipos do dicionário, a Function escreve a prata com o
  DuckDB (`COPY ... TO 'abfss://...' (FORMAT parquet)`), e o ADF fica com o download e a bronze.
- **Consequência:** o ADF faz menos e a Function faz mais. A reconciliação não muda.

## D3. Janela de reprocessamento no lugar de watermark, com dois gatilhos

- **Contexto:** a CVM atualiza M e M-1 todos os dias (segunda a sábado, 08:00) e M-2 a M-11 uma
  vez por semana. O mais antigo não muda. O arquivo não tem coluna de alteração (um `updated_at`)
  para um watermark clássico: cada linha só tem a data de competência.
- **Decisão:** uma **janela de reprocessamento** (*lookback window*) faz o papel do watermark: o
  que é anterior a M-11 está congelado e não é relido. Um pipeline recebe a janela por parâmetro,
  e dois gatilhos de agenda seguem o contrato da fonte: diário (M e M-1, segunda a sábado às
  10:00, duas horas depois da publicação) e semanal (M-2 a M-11, domingo às 10:00).
- **Consequência:** a correção não depende de detectar mudança. Recarregar a janela inteira
  substitui o que mudou e regrava igual o que não mudou (D6). Custo: ~12 downloads de ~11 MB por
  semana. Pular o mês cujo `ETag` não mudou é uma otimização futura, e o manifesto já guarda o
  `ETag` para isso.
- **Premissas e riscos aceitos:**
  - O contrato diz só "semanalmente". A regravação observada foi num sábado (14/11/2026), e o
    gatilho de domingo parte disso. Se a CVM mudar o dia, a janela semanal fica até uma semana
    atrasada. A comparação por `ETag` em todos os dias é a saída.
  - Se a CVM publicar com atraso num dia, o gatilho diário pega a versão anterior, e a correção
    chega no dia seguinte.

## D4. Chave natural com a subclasse

- **Contexto:** desde a Resolução CVM 175, um fundo tem classes e subclasses. No arquivo de
  set/2026, `CNPJ_FUNDO_CLASSE` + `DT_COMPTC` tem 8.013 duplicados. Com `ID_SUBCLASSE`, zero.
- **Decisão:** a chave da prata é `(CNPJ_FUNDO_CLASSE, ID_SUBCLASSE, DT_COMPTC)`, com subclasse
  vazia tratada como valor (não como "qualquer").
- **Consequência:** a validação confere a unicidade dessa chave a cada carga. Se a CVM publicar
  uma duplicata, a carga falha em vez de escolher uma linha em silêncio.

## D5. Bronze imutável e versionada por carga

- **Contexto:** uma republicação substitui o arquivo na origem, e a versão anterior some. Sem
  guardá-la, não dá para responder "o que a CVM dizia na segunda?" nem reprocessar sem baixar
  de novo.
- **Decisão:** cada carga grava o ZIP original em
  `bronze/.../competencia=AAAAMM/carga=<RUN_ID>/`, sem sobrescrever. Uma regra de ciclo de vida
  do armazenamento apaga as versões com mais de 90 dias.
- **Consequência:** a bronze cresce ~11 MB por mês carregado por dia (ordem de 1 GB no teto de
  90 dias), centavos por mês. A prova de republicação confere que as duas versões estão lá.

## D6. Prata substituída por mês inteiro, só depois de validada

- **Contexto:** append numa carga que relê o mês duplicaria tudo. Um MERGE por chave exigiria um
  formato de tabela com transação (Delta Lake ou Iceberg) e, no ADF, um Data Flow (D2).
- **Decisão:** a prata é particionada por mês de competência, e cada carga **substitui a
  partição inteira**. Por mês, um pipeline filho (`pl_inf_diario_mes`) copia a versão nova para a
  staging, a Function a valida contra a bronze, e só então o ADF apaga a partição e copia a
  staging para ela. O pipeline pai tem `concurrency: 1`, e cada mês grava só nos próprios
  caminhos (staging e manifesto com a competência no caminho).
- **Consequência:**
  - a carga é idempotente (reexecutar dá o mesmo resultado);
  - uma linha removida na republicação some da prata, o que um MERGE só por upsert não faria;
  - um dado que não passa na validação nunca substitui o bom;
  - duas execuções nunca mexem no mesmo mês ao mesmo tempo.
- **Limite:** entre o Delete e o Copy final, um leitor concorrente veria a partição vazia, e se o
  Copy final falhar o mês fica vazio até a próxima carga (o alerta avisa). O único leitor é a
  Function, que roda depois. Com leitores concorrentes, a evolução é Delta Lake, em que a
  substituição da partição é atômica.
- **Custo da validação antes da troca:** uma chamada à Function a mais por carga de mês.

## D7. A prova: contagem e SHA-256 canônico por mês, e republicação simulada

- **Contexto:** "a carga rodou" não prova que o dado está certo. Comparar o arquivo não
  serve, porque o CSV e o Parquet não têm os mesmos bytes.
- **Decisão:** para cada mês, a Function calcula na bronze (CSV) e no Parquet (staging, e depois
  prata) a contagem de linhas e o SHA-256 de uma **forma canônica**: linhas ordenadas pela chave
  (D4), cada campo num formato fixo (decimal com a escala do dicionário, data ISO, nulo
  explícito). Também confere que a chave é única e que o ouro soma as mesmas linhas da prata.
  Divergência faz a Function responder erro e o pipeline falhar. O resultado vai para
  `controle/`, um arquivo por mês.
- **Republicação simulada:** a CVM não reapresenta um mês a pedido. A prova usa duas versões
  do mesmo mês: T0 (a real) e T1 (T0 com valores alterados, uma linha removida e uma incluída).
  Passam em sequência, e o teste cobra: a prata igual a T1; os outros meses com o hash anterior;
  a reexecução sem mudança; a bronze com as duas versões.
- **Consequência:** a prova é a mesma na máquina e na Azure, porque é o mesmo módulo Python
  (D2). Declarado: a republicação é simulada, e o hash só vale com os tipos da prata confirmados
  (D2b).

## D8. Infraestrutura em Terraform, dois ambientes iguais

- **Contexto:** dev e prod precisam ser a mesma coisa com nomes e tamanhos diferentes, e o
  ambiente tem de poder ser recriado e destruído por comando.
- **Decisão:** Terraform com o provider `azurerm`, um módulo para o ambiente e um arquivo de
  variáveis por ambiente. Cada ambiente tem o próprio resource group, conta de armazenamento do
  lake, ADF, Function (com o armazenamento do host), Key Vault, alerta e orçamento. O estado remoto
  fica num container à parte, criado no passo 1 do runbook.
- **Consequência:** `terraform destroy` encerra tudo, o que importa para não deixar custo
  correndo. Os IDs de assinatura e tenant ficam fora do repositório.

## D9. O JSON do ADF no Git é a fonte da verdade; a publicação é pelo pacote da Microsoft

- **Contexto:** no fluxo antigo do ADF, alguém clica em "Publish" no ADF Studio, que gera o ARM
  no branch `adf_publish`, e o deploy parte dali, com um passo manual no meio. A Microsoft
  documenta a alternativa: o pacote npm `@microsoft/azure-data-factory-utilities` valida a
  fábrica e exporta o ARM no CI
  ([Automated publishing for CI/CD](https://learn.microsoft.com/en-us/azure/data-factory/continuous-integration-delivery-improvements)).
- **Decisão:** os recursos do ADF (linked services, datasets, pipelines, gatilhos) ficam em JSON
  no repositório. O build valida e exporta o ARM com o pacote. Nenhum ambiente é editado à mão:
  dev e prod recebem o mesmo ARM, com parâmetros diferentes.
- **Consequência:** o validador confere o JSON (esquema e referências entre recursos) e gera o
  ARM. Não prova a execução, os tipos nem as permissões. Se ele roda sem conexão com a Azure é
  algo a confirmar. Antes do deploy, os gatilhos são parados, e voltam depois, com o script em
  PowerShell que a Microsoft publica para isso
  ([sample pre- and post-deployment script](https://learn.microsoft.com/en-us/azure/data-factory/continuous-integration-delivery-sample-script)).

## D10. GitHub para o código; Azure Pipelines para o deploy, com agente próprio e federação

- **Contexto:** o código público mora no GitHub. O Azure DevOps lê repositórios do GitHub, e o
  Azure Pipelines é o CI/CD nativo da Azure, com ambientes e aprovação. O agente hospedado pela
  Microsoft exige a organização ligada a uma assinatura da Azure, e o *tier* gratuito dele tem
  1.800 minutos por mês. O *tier* auto-hospedado tem 1 job paralelo, sem limite de tempo, numa
  máquina própria
  ([parallel jobs](https://learn.microsoft.com/en-us/azure/devops/pipelines/licensing/concurrent-jobs)).
- **Decisão:** o GitHub Actions roda o CI (lint, tipos, testes, validações) a cada push. O Azure
  Pipelines faz o build do ARM e o deploy em dev, espera a aprovação do ambiente `prod` e faz o
  deploy em prod. O agente é auto-hospedado (uma máquina Linux com `pwsh` e o módulo Az, para o
  script dos gatilhos) e a service connection usa federação de identidade (*workload identity
  federation*), sem senha guardada.
- **Agente próprio e repositório público:** por padrão, o Azure Pipelines dispara em PR vindo de
  fork ([GitHub repositories](https://learn.microsoft.com/en-us/azure/devops/pipelines/repos/github)).
  O pipeline de deploy usa `pr: none`, e a organização desliga o build de PR de fork (*Limit
  building pull requests from forked GitHub repositories*). Assim o agente, que tem a credencial
  de deploy, nunca roda código de terceiros.
- **Consequência:** nenhum segredo de deploy no Azure DevOps. A máquina do agente é de quem
  implanta. O CI do GitHub é o selo público; o Azure Pipelines é o caminho de produção, descrito
  no runbook.

## D11. Key Vault só para o que é segredo

- **Contexto:** a fonte é pública e anônima, e o acesso ao lake é por identidade gerenciada.
  O único segredo é a chave da Function que o ADF usa para chamá-la.
- **Decisão:** a chave da Function fica no Key Vault (autorização por RBAC, não por política de
  acesso), e o ADF a lê com a própria identidade gerenciada.
- **Alternativa avaliada e descartada:** a atividade Azure Function aceita identidade gerenciada
  ([Azure Function activity](https://learn.microsoft.com/en-us/azure/data-factory/control-flow-azure-function-activity)),
  mas a forma segura exige registro de aplicativo e autenticação na própria Function (*Easy
  Auth*): mais peças que um segredo no Key Vault. Fica o Key Vault.

## D12. O que fica fora, e por quê

| Fora | Por quê |
|---|---|
| Data Flow, Databricks, Synapse | volume pequeno; a Copy e o DuckDB bastam (D2) |
| Data Factory no Microsoft Fabric | roda sobre uma capacidade do Fabric, outro modelo de contratação; o case é sobre o ADF da Azure, que cobra por execução |
| VNet gerenciada e endpoint privado | custo; ficam como evolução de rede |
| Log Analytics e Purview | custo; o alerta por métrica e os manifestos em `controle/` cobrem a operação deste caso |
| Delta Lake na prata | exigiria Data Flow ou Spark para escrever; registrado como evolução (D6) |
| Histórico anual (`DADOS/HIST/`) | a tese é sobre a janela que muda; o histórico não muda |

## D13. Desenho de referência, validado localmente

- **Contexto:** o ADF não tem emulador, e o Azurite não implementa o namespace hierárquico do
  ADLS Gen2. Executar na Azure exige uma assinatura ativa e a destruição do ambiente depois.
- **Decisão:** o repositório entrega o desenho, o código e a validação de tudo que roda fora da
  nuvem: a lógica e a prova com o dado real da CVM, o Terraform (`validate`, testes com
  *mock provider*, análise estática), o JSON do ADF (validador e export do ARM) e o YAML do
  pipeline. O passo a passo para implantar está em [`runbook.md`](runbook.md).
- **Consequência:** o que só a Azure confirma está listado em [`testes.md`](testes.md).
