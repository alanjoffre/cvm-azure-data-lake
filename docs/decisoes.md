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

- **Contexto:** o ADF faz bem buscar, descompactar, tipar e gravar (atividade Copy), e cobra por
  execução de atividade. A agregação e a prova (hash canônico, contagem por chave) exigem
  lógica. No ADF, as opções são Data Flow (Spark gerenciado, ~US$ 2 por hora de cluster, com
  minutos de partida) ou chamar código externo.
- **Decisão:** o ADF faz as cópias (origem → bronze → prata). Uma Azure Function em Python, no
  plano de consumo, faz o ouro e a reconciliação com DuckDB, e o ADF a chama pela atividade
  Azure Function. Com ~540 mil linhas por mês, o DuckDB resolve em memória em segundos.
- **Consequência:** nenhum cluster ligado e uma lógica testável com pytest, na máquina. O mesmo
  módulo Python roda localmente (pastas) e na Function (`abfss://`). Limite: a atividade Azure
  Function do ADF espera resposta em até 230 segundos, folga ampla para um mês por chamada.
- **Fora:** Data Flow, Databricks e Synapse. Seriam a escolha com volume de dezenas de GB por
  carga, o que não é o caso aqui.

## D3. Janela mutável como watermark, com dois gatilhos

- **Contexto:** a CVM atualiza M e M-1 todos os dias (segunda a sábado, 08:00) e M-2 a M-11 uma
  vez por semana. O mais antigo não muda. Um watermark por data de modificação (a coluna
  "última alteração" das cargas incrementais clássicas) não existe no arquivo: cada linha só
  tem a data de competência.
- **Decisão:** o watermark é a **fronteira da janela mutável**. Tudo que é anterior a M-11 está
  congelado e não é relido. Um único pipeline recebe a janela por parâmetro, e dois gatilhos de
  agenda seguem o contrato da fonte: diário (M e M-1, segunda a sábado às 10:00, duas horas
  depois da publicação) e semanal (M-2 a M-11, domingo às 10:00).
- **Consequência:** a correção não depende de detectar mudança. Recarregar a janela inteira
  sobrescreve o que mudou e reescreve igual o que não mudou (D6). Custo: ~12 downloads de ~11 MB
  por semana. Detectar mudança pelo `ETag` e pular o mês igual é uma otimização registrada como
  futura, e o manifesto já guarda o `ETag` para isso.
- **Risco aceito:** se a CVM publicar com atraso num dia, o gatilho diário pega a versão
  anterior, e a correção chega no dia seguinte.

## D4. Chave natural com a subclasse

- **Contexto:** desde a Resolução CVM 175, um fundo tem classes e subclasses. No arquivo de
  set/2026, `CNPJ_FUNDO_CLASSE` + `DT_COMPTC` tem 8.013 duplicados. Com `ID_SUBCLASSE`, zero.
- **Decisão:** a chave da prata é `(CNPJ_FUNDO_CLASSE, ID_SUBCLASSE, DT_COMPTC)`, com subclasse
  vazia tratada como valor (não como "qualquer").
- **Consequência:** a reconciliação confere a unicidade dessa chave a cada carga. Se a CVM
  publicar uma duplicata, a carga falha em vez de escolher uma linha em silêncio.

## D5. Bronze imutável e versionada por carga

- **Contexto:** uma republicação substitui o arquivo na origem, e a versão anterior some. Sem
  guardá-la, não dá para responder "o que a CVM dizia na segunda?" nem reprocessar sem baixar
  de novo.
- **Decisão:** cada carga grava o ZIP original em
  `bronze/.../competencia=AAAAMM/carga=<RUN_ID>/`, sem sobrescrever. Uma regra de ciclo de vida
  do armazenamento apaga as versões com mais de 90 dias.
- **Consequência:** a bronze cresce ~11 MB por mês carregado por dia (ordem de 1 GB no teto de
  90 dias), centavos por mês. A prova de republicação confere que as duas versões estão lá.

## D6. Prata substituída por mês inteiro, em Parquet

- **Contexto:** append numa carga que relê o mês duplicaria tudo. Um MERGE por chave exigiria um
  formato de tabela com transação (Delta Lake ou Iceberg) e, no ADF, um Data Flow (D2).
- **Decisão:** a prata é particionada por mês de competência, e cada carga **substitui a
  partição inteira**. O ADF copia a versão nova para uma pasta de staging, apaga a partição e
  copia o staging para ela. O formato é Parquet com os tipos do dicionário da CVM.
- **Consequência:** a carga é idempotente (reexecutar dá o mesmo resultado), e uma linha
  removida na republicação some da prata, o que um MERGE só por upsert não faria. Limite: entre
  o Delete e o último Copy, um leitor concorrente veria a partição vazia. O único leitor é a
  Function, que roda depois. Com leitores concorrentes, a evolução é Delta Lake, em que a
  substituição da partição é atômica.

## D7. A prova: contagem e SHA-256 canônico por mês, e republicação simulada

- **Contexto:** "a carga rodou" não prova que o dado está certo. Comparar o arquivo não
  serve, porque o CSV e o Parquet não têm os mesmos bytes.
- **Decisão:** para cada mês, a reconciliação calcula na bronze (CSV) e na prata (Parquet) a
  contagem de linhas e o SHA-256 de uma **forma canônica**: linhas ordenadas pela chave (D4),
  cada campo num formato fixo (decimal com a escala do dicionário, data ISO, nulo explícito).
  Também confere que a chave é única e que o ouro soma as mesmas linhas da prata. Divergência
  faz o pipeline falhar. O resultado vai para `controle/`.
- **Republicação simulada:** a CVM não reapresenta um mês a pedido. A prova usa duas versões
  do mesmo mês: T0 (a real) e T1 (T0 com valores alterados, uma linha removida e uma incluída).
  Passam em sequência, e o teste cobra: a prata igual a T1; os outros meses com o hash anterior;
  a reexecução sem mudança; a bronze com as duas versões.
- **Consequência:** a prova é a mesma na máquina e na Azure, porque é o mesmo módulo Python
  (D2). Declarado: a republicação é simulada.

## D8. Infraestrutura em Terraform, dois ambientes iguais

- **Contexto:** dev e prod precisam ser a mesma coisa com nomes e tamanhos diferentes, e o
  ambiente tem de poder ser recriado e destruído por comando.
- **Decisão:** Terraform com o provider `azurerm`, um módulo para o ambiente e um arquivo de
  variáveis por ambiente. Cada ambiente tem o próprio resource group, conta de armazenamento,
  ADF, Function, Key Vault, alerta e orçamento. O estado remoto fica num container à parte,
  criado no passo 1 do runbook.
- **Consequência:** `terraform destroy` encerra tudo, o que importa para não deixar custo
  correndo. Os IDs de assinatura e tenant ficam fora do repositório.

## D9. O JSON do ADF no Git é a fonte da verdade; a publicação é pelo pacote da Microsoft

- **Contexto:** no fluxo antigo do ADF, alguém clica em "Publish" no ADF Studio, que gera o ARM
  no branch `adf_publish`, e o deploy parte dali, com um passo manual no meio. A Microsoft
  documenta a alternativa: o pacote npm `@microsoft/azure-data-factory-utilities` valida a
  fábrica e exporta o ARM no CI.
- **Decisão:** os recursos do ADF (linked services, datasets, pipeline, gatilhos) ficam em JSON
  no repositório, no formato da integração Git. O build valida e exporta o ARM com o pacote. O
  ADF de dev pode ser ligado ao GitHub para editar no ADF Studio. O de prod nunca é editado à
  mão e só recebe o ARM.
- **Consequência:** a mesma validação roda no CI do GitHub e na máquina (sem conta na Azure, a
  confirmar na F4). Antes do deploy, os gatilhos são parados, e voltam depois, com o script
  que a Microsoft publica para isso.

## D10. GitHub para o código; Azure Pipelines para o deploy, com agente próprio e federação

- **Contexto:** o código público mora no GitHub. O Azure DevOps lê repositórios do GitHub, e o
  Azure Pipelines é o CI/CD nativo da Azure, com ambientes e aprovação. Um agente hospedado pela
  Microsoft exige assinatura com cobrança ativa ou um pedido de concessão gratuita, que leva
  dias.
- **Decisão:** o GitHub Actions roda o CI (lint, tipos, testes, validações) a cada push. O Azure
  Pipelines faz o build do ARM e o deploy em dev, espera a aprovação do ambiente `prod` e faz o
  deploy em prod. O agente é auto-hospedado (uma máquina Linux) e a service connection usa
  federação de identidade (*workload identity federation*), sem senha guardada.
- **Consequência:** nenhum segredo de deploy no Azure DevOps, e nenhum custo de agente. PR de
  fork não dispara o pipeline (o agente próprio não roda código de terceiros). O CI do GitHub
  é o selo público. O Azure Pipelines é o caminho de produção, descrito no runbook.

## D11. Key Vault só para o que é segredo

- **Contexto:** a fonte é pública e anônima, e o acesso ao lake é por identidade gerenciada.
  O único segredo é a chave da Function que o ADF usa para chamá-la.
- **Decisão:** a chave da Function fica no Key Vault (autorização por RBAC, não por política de
  acesso), e o ADF a lê com a própria identidade gerenciada.
- **Alternativa registrada:** se a atividade Azure Function aceitar autenticação por identidade
  gerenciada nesse cenário, o Key Vault pode sair e o desenho fica sem nenhum segredo (a
  conferir na F4).

## D12. O que fica fora, e por quê

| Fora | Por quê |
|---|---|
| Data Flow, Databricks, Synapse, Fabric | volume pequeno; a Copy e o DuckDB bastam (D2) |
| VNet gerenciada e endpoint privado | custo; o desenho de rede está descrito como evolução na arquitetura |
| Log Analytics e Purview | custo; o alerta por métrica e o manifesto em `controle/` cobrem a operação deste caso |
| Delta Lake na prata | exigiria Data Flow ou Spark para escrever; registrado como evolução (D6) |
| Histórico anual (`DADOS/HIST/`) | a tese é sobre a janela que muda; o histórico não muda |

## D13. Desenho de referência, validado localmente

- **Contexto:** o ADF não tem emulador, e o Azurite não implementa o namespace hierárquico do
  ADLS Gen2. Executar na Azure exige uma assinatura ativa e a destruição do ambiente depois.
- **Decisão:** o repositório entrega o desenho, o código e a validação de tudo que roda fora da
  nuvem: a lógica e a prova com o dado real da CVM, o Terraform (`validate`, testes com
  *mock provider*, análise estática), o ADF (validador e export do ARM) e o YAML do pipeline.
  O passo a passo para implantar está em [`runbook.md`](runbook.md).
- **Consequência:** o README declara "não aplicado na Azure". O que só a Azure confirma está
  listado em [`testes.md`](testes.md).
