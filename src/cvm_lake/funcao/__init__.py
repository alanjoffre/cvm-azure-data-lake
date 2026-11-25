"""Lógica que roda dentro da Azure Function (D2): validar, reconciliar e refazer o ouro.

Na Azure, o ADF chama a Function pela atividade Azure Function, que espera um objeto JSON; uma
Function por HTTP tem até 230 segundos para responder. Por isso as duas entradas públicas
(``validar`` e ``reconciliar``) devolvem sempre um dicionário serializável com ``ok``,
contagens, hashes e o tempo gasto.
"""
