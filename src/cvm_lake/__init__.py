"""Carga do Informe Diário da CVM em camadas, com prova por contagem e SHA-256 canônico.

Dois papéis, separados como no desenho (docs/arquitetura.md, D2):

- ``cvm_lake.funcao``: a lógica que, na Azure, roda dentro da Azure Function (validação, ouro,
  reconciliação). É o código de produção.
- ``cvm_lake.adf_local``: o substituto local das atividades do ADF (Copy, Delete, ForEach), que
  existe só para exercitar a lógica na máquina, com as mesmas pastas e a mesma ordem de passos.
"""
