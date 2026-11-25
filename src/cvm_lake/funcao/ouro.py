"""Ouro: indicadores por fundo/classe, subclasse e mês, refeitos a partir da prata.

Uma linha por (CNPJ_FUNDO_CLASSE, ID_SUBCLASSE) no mês. A coluna ``dias_com_informe`` conta as
linhas da prata que formaram o agregado: a reconciliação cobra que a soma dela seja igual à
contagem da prata, e que captação e resgate somem igual nos dois (decimal exato, sem float).
"""

from __future__ import annotations

SQL_OURO = """
SELECT
    p."CNPJ_FUNDO_CLASSE"                              AS cnpj_fundo_classe,
    p."ID_SUBCLASSE"                                   AS id_subclasse,
    -- arg_max(valor, DT_COMPTC): o valor do dia mais recente do mês (o tipo pode mudar no mês)
    arg_max(p."TP_FUNDO_CLASSE", p."DT_COMPTC")        AS tp_fundo_classe,
    min(p."DT_COMPTC")                                 AS primeiro_dia,
    max(p."DT_COMPTC")                                 AS ultimo_dia,
    CAST(count(*) AS INTEGER)                          AS dias_com_informe,
    arg_max(p."VL_PATRIM_LIQ", p."DT_COMPTC")          AS pl_ultimo_dia,
    arg_max(p."NR_COTST", p."DT_COMPTC")               AS cotistas_ultimo_dia,
    CAST(sum(p."CAPTC_DIA") AS DECIMAL(38,2))          AS captacao,
    CAST(sum(p."RESG_DIA") AS DECIMAL(38,2))           AS resgate,
    CAST(sum(p."CAPTC_DIA") - sum(p."RESG_DIA") AS DECIMAL(38,2)) AS captacao_liquida,
    -- Variação da cota entre o primeiro e o último dia com informe no mês (limite declarado em
    -- docs/arquitetura.md, seção 9). Cota inicial zero ou negativa (há casos no arquivo) → nulo.
    CASE WHEN arg_min(p."VL_QUOTA", p."DT_COMPTC") > 0
         THEN CAST(arg_max(p."VL_QUOTA", p."DT_COMPTC") AS DOUBLE)
              / CAST(arg_min(p."VL_QUOTA", p."DT_COMPTC") AS DOUBLE) - 1
    END                                                AS variacao_cota
FROM prata AS p
GROUP BY p."CNPJ_FUNDO_CLASSE", p."ID_SUBCLASSE"
ORDER BY p."CNPJ_FUNDO_CLASSE", p."ID_SUBCLASSE" NULLS FIRST
"""

# Prata × ouro: mesmas linhas e mesmas somas exatas.
SQL_CONFERE_OURO = """
SELECT
    (SELECT count(*) FROM prata)                                   AS linhas_prata,
    (SELECT coalesce(sum(dias_com_informe), 0) FROM ouro)          AS linhas_no_ouro,
    (SELECT coalesce(sum("CAPTC_DIA"), 0) FROM prata)              AS captacao_prata,
    (SELECT coalesce(sum(captacao), 0) FROM ouro)                  AS captacao_ouro,
    (SELECT coalesce(sum("RESG_DIA"), 0) FROM prata)               AS resgate_prata,
    (SELECT coalesce(sum(resgate), 0) FROM ouro)                   AS resgate_ouro,
    (SELECT count(*) FROM ouro)                                    AS linhas_ouro
"""
