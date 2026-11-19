# Como se testa

O ADF não tem emulador, e o Azurite não implementa o namespace hierárquico do ADLS Gen2 (D13).
Por isso cada parte é validada pela ferramenta que a confere sem a nuvem, e o que só a Azure
confirma fica listado à parte.

## Na máquina e no CI

| Parte | Como se testa | Prova de quê |
|---|---|---|
| Carga, janela mutável, republicação, contagem e hash | pytest com dados sintéticos no formato da CVM, e a prova completa com os arquivos reais baixados da CVM | a tese: reprocessa sem duplicar e sem perder |
| Bronze, prata e ouro | pastas locais com o mesmo layout do ADLS. O código só troca a raiz (pasta local ou `abfss://`). Ouro em DuckDB | camadas, partições e agregados |
| Terraform | `terraform fmt -check`, `terraform validate`, tflint, Checkov e `terraform test` com `mock_provider` (plano sem conta) | infraestrutura coerente e com as regras de segurança do desenho |
| ADF (JSON) | `@microsoft/azure-data-factory-utilities` (validate + export do ARM) e testes próprios de referência entre recursos e parâmetros | o ADF aceitaria a fábrica e o deploy |
| ARM gerado | arm-ttk | boas práticas do template |
| Azure Pipelines (YAML) | validação contra o esquema publicado; os scripts das etapas rodam no CI do GitHub | arquivo bem formado e scripts funcionando |
| Tudo | GitHub Actions a cada push | selo verde |

## Só na Azure

- O ADF executando: expressões, Copy de ZIP para Parquet, ForEach e gatilhos.
- Permissões reais: identidade gerenciada no ADLS e no Key Vault, chave compartilhada desligada.
- A Function lendo e gravando em `abfss://`.
- O Azure Pipelines com agente próprio, federação de identidade, aprovação e deploy de dev para
  prod.
- O alerta de falha chegando por e-mail.

O roteiro para isso está em [`runbook.md`](runbook.md).

## O que a prova da tese cobra

Para cada mês carregado:
1. a contagem de linhas da bronze (CSV) é igual à da prata (Parquet);
2. o SHA-256 canônico (D7) da bronze é igual ao da prata;
3. a chave `(CNPJ_FUNDO_CLASSE, ID_SUBCLASSE, DT_COMPTC)` é única na prata;
4. o ouro soma as mesmas linhas da prata.

Na republicação simulada (T0 → T1 do mesmo mês):
1. a prata do mês é igual a T1;
2. os outros meses mantêm o hash anterior;
3. reexecutar não muda nenhum hash;
4. a bronze guarda as duas versões.

Limite: a tese é provada pela execução local da mesma lógica, nas mesmas pastas e com a mesma
janela. A equivalência com o ADF executando só se confirma implantando.
