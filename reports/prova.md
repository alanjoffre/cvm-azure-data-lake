# Relatório da prova local

Gerado por `uv run cvm-lake prova` em 2026-11-24T21:50:00+00:00. Este relatório mede o substituto local (DuckDB e pastas). Nada foi aplicado na Azure.

- Resultado: **passou** (32 de 32 checagens)
- Código: commit `44d87b258fb0`
- Ambiente: Python 3.12.16, DuckDB 2.0.1, Linux x86_64
- Mês republicado na simulação: 202610

## Os arquivos (snapshot T0)

| Competência | Linhas | ZIP (bytes) | CSV (bytes) | Fundos/classes | Pares fundo + subclasse | Datas | Sem subclasse | Last-Modified | ETag |
|---|---:|---:|---:|---:|---:|---|---:|---|---|
| 202609 | 538.758 | 11.636.622 | 54.532.914 | 25.594 | 26.016 | 22 (2026-09-01 a 2026-09-30) | 518.548 | Sat, 21 Nov 2026 09:03:04 GMT | `"6b015e48-b18f8e"` |
| 202610 | 538.570 | 11.785.300 | 54.572.835 | 25.526 | 25.977 | 22 (2026-10-01 a 2026-10-30) | 516.942 | Tue, 24 Nov 2026 03:57:36 GMT | `"6b050b30-b3d454"` |
| 202611 | 329.155 | 7.189.543 | 33.327.318 | 25.326 | 25.777 | 15 (2026-11-02 a 2026-11-23) | 315.367 | Tue, 24 Nov 2026 03:55:35 GMT | `"6b050ab7-6db427"` |

SHA-256 canônico de cada mês em T0 (DuckDB e referência Python):

- 202609: `4a38b432ac9783b93093665c355bff8dbadc0dc2dcfe4c698e36f5a545f63576`
- 202610: `eecb22285c67f1cbebd98c40ea1d2515b0599f97dec6d7eb15b4a54264ebea5e`
- 202611: `9ddcd09d946a8a8b743d0dbf6b252952974ce0799e4a25935b7eaf13c628f961`

## Republicação simulada

T1 de 202610: 3 linhas alteradas (+1.000,00 em `VL_PATRIM_LIQ` e `CAPTC_DIA`), 1 removida e 1 incluída. Mesma contagem de T0 (538.570), hash `6f51a47a44c12a7a…`.

## Checagens

| | Checagem | Detalhe |
|---|---|---|
| ok | T0 202609: manifesto aponta a carga | 24a73c28-8a17-410e-81e6-edca47032e13 |
| ok | T0 202609: referência Python = DuckDB | 538758 linhas, 4a38b432ac9783b9… × 538758, 4a38b432ac9783b9… |
| ok | T0 202609: staging apagada no fim | carga=24a73c28-8a17-410e-81e6-edca47032e13 |
| ok | T0 202610: manifesto aponta a carga | 24a73c28-8a17-410e-81e6-edca47032e13 |
| ok | T0 202610: referência Python = DuckDB | 538570 linhas, eecb22285c67f1cb… × 538570, eecb22285c67f1cb… |
| ok | T0 202610: staging apagada no fim | carga=24a73c28-8a17-410e-81e6-edca47032e13 |
| ok | T0 202611: manifesto aponta a carga | 24a73c28-8a17-410e-81e6-edca47032e13 |
| ok | T0 202611: referência Python = DuckDB | 329155 linhas, 9ddcd09d946a8a8b… × 329155, 9ddcd09d946a8a8b… |
| ok | T0 202611: staging apagada no fim | carga=24a73c28-8a17-410e-81e6-edca47032e13 |
| ok | reexecução 202609: mesmo hash e mesma contagem | 538758 linhas, 4a38b432ac9783b9… |
| ok | reexecução 202609: ouro idêntico | 7b36d7404ccdf099… |
| ok | reexecução 202609: 2 versões na bronze, manifesto na nova | 2 versões; vigente 9fcbbb91-8a53-4276-8cd3-9256bb33b948 |
| ok | reexecução 202610: mesmo hash e mesma contagem | 538570 linhas, eecb22285c67f1cb… |
| ok | reexecução 202610: ouro idêntico | e8adf64a9e1e1215… |
| ok | reexecução 202610: 2 versões na bronze, manifesto na nova | 2 versões; vigente 9fcbbb91-8a53-4276-8cd3-9256bb33b948 |
| ok | reexecução 202611: mesmo hash e mesma contagem | 329155 linhas, 9ddcd09d946a8a8b… |
| ok | reexecução 202611: ouro idêntico | 1e376cc7d4d87251… |
| ok | reexecução 202611: 2 versões na bronze, manifesto na nova | 2 versões; vigente 9fcbbb91-8a53-4276-8cd3-9256bb33b948 |
| ok | T1 202609: mês não republicado ficou igual | 4a38b432ac9783b9… |
| ok | T1 202610: prata = T1 (hash calculado à parte) | 538570 linhas, 6f51a47a44c12a7a… |
| ok | T1 202610: contagem igual à de T0 e hash diferente | 538570 = 538570; eecb22285c67… ≠ 6f51a47a44c1… |
| ok | T1 202610: ouro refeito | hash do ouro mudou |
| ok | T1 202611: mês não republicado ficou igual | 9ddcd09d946a8a8b… |
| ok | T1 202610: linha removida ausente da prata | ('67.657.950/0001-64', '', '2026-10-23') |
| ok | T1 202610: linha incluída presente na prata | ('34.246.534/0001-14', '', '2026-10-12') |
| ok | T1 202610: linha alterada com o valor novo | ('60.762.973/0001-90', '', '2026-10-14'): ('404727237.11', '0.00') → ('404728237.11', '1000.00') |
| ok | T1 202610: linha alterada com o valor novo | ('37.987.887/0001-63', '', '2026-10-29'): ('14669207.00', '0.00') → ('14670207.00', '1000.00') |
| ok | T1 202610: linha alterada com o valor novo | ('10.446.982/0001-01', '', '2026-10-29'): ('11036873.96', '0.00') → ('11037873.96', '1000.00') |
| ok | divergência 202610: a validação falhou pelo hash | sha256: bronze 6f51a47a44c1… × staging 712c2946ffc8… |
| ok | divergência 202610: manifesto continua na carga T1 | 5d30790c-1ba3-46b2-9484-917fdde511b4 |
| ok | divergência 202610: prata intacta (= T1) | 6f51a47a44c12a7a… |
| ok | divergência 202610: staging preservada para diagnóstico | carga=c3c2e7de-66a6-4876-959e-5dac4612f4e6 |

## Tempos por mês

`funcao_s` = validar + reconciliar, o trabalho que a Function fará. Medido nesta máquina, não na Azure: a folga contra os 230 s que o ADF espera pela resposta da Function se confirma na execução na Azure.

| Carga | Competência | Pipeline (s) | Validar (s) | Reconciliar (s) | Função (s) |
|---|---|---:|---:|---:|---:|
| T0 | 202609 | 2.868 | 1.256 | 1.255 | 2.511 |
| T0 | 202610 | 2.724 | 1.176 | 1.2 | 2.376 |
| T0 | 202611 | 1.931 | 0.8 | 0.883 | 1.683 |
| reexecução | 202609 | 2.878 | 1.216 | 1.31 | 2.526 |
| reexecução | 202610 | 2.811 | 1.171 | 1.27 | 2.441 |
| reexecução | 202611 | 2.012 | 0.879 | 0.869 | 1.748 |
| T1 | 202609 | 2.776 | 1.184 | 1.235 | 2.419 |
| T1 | 202610 | 2.87 | 1.2 | 1.28 | 2.48 |
| T1 | 202611 | 1.994 | 0.819 | 0.889 | 1.708 |

Maior tempo de função: 2.526 s (teto 230 s).
