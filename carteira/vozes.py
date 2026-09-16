"""As pitadas de mitologia grega nos avisos proativos.

O agente tem a personalidade no prompt dele. Estes textos não passam por
modelo nenhum — são escritos aqui e enviados pelo beat —, então a voz da
Dracma precisa existir também neste arquivo, ou ela vira outra pessoa toda
vez que o aviso é automático.

Duas regras de dosagem, e a segunda é a que mais importa:

1. A referência vem DEPOIS do número. O aviso é útil primeiro e charmoso
   depois; quem está estourando o orçamento quer ver quanto, não um mito.
2. A escolha é DETERMINÍSTICA, a partir da chave do alerta, e ROTACIONA por
   período. Sorteio faria a mesma pessoa ouvir "Ícaro" três meses seguidos por
   azar, e um `random` em código de produção ainda vira VULNERABILITY no
   Sonar. Só o hash da chave também não bastava: com quatro frases, medindo
   doze meses do mesmo limite, uma delas caiu três vezes seguidas. O `passo`
   resolve — a chave decide onde a pessoa começa, o período anda dali.
"""

from __future__ import annotations

import hashlib

ESTOUROU = (
    "Ícaro também achou que dava pra subir mais um pouco.",
    "As Sereias cantaram e você foi — acontece com os melhores navegadores.",
    "Dioniso aprovaria. Seu orçamento, nem tanto.",
    "O Minotauro deste labirinto atende por esta categoria.",
)

PERTO = (
    "Ainda dá pra desviar do rochedo.",
    "Ariadne te deu o fio: é só seguir até o fim do mês.",
    "Prometeu roubou o fogo e se deu mal. Você ainda está em tempo.",
    "Aqui é onde Ícaro costuma decidir se sobe ou não.",
)

MES_NO_AZUL = (
    "Atena aprovaria o plano.",
    "Mês conduzido como quem conhece a rota.",
    "Midas ficaria com inveja da semana.",
)

MES_NO_VERMELHO = (
    "Sísifo também empurra a pedra de novo todo mês.",
    "Penélope desfazia o bordado à noite. Dá pra recomeçar amanhã.",
    "Nem toda travessia é tranquila — o mapa continua aí.",
)


def escolher(frases: tuple[str, ...], chave: str, passo: int = 0) -> str:
    """Uma frase estável para esta chave, andando a cada `passo`.

    `passo` é o período (o mês, em geral): períodos seguidos nunca repetem a
    frase, o que só o hash não garantia.

    `blake2b` e não `hash()`: o `hash()` de string é salgado por processo no
    Python, então o mesmo alerta sairia com frases diferentes em cada worker
    do Celery — e o teste passaria na sua máquina e falharia no CI.
    """
    digest = hashlib.blake2b(chave.encode(), digest_size=4).digest()
    return frases[(int.from_bytes(digest, "big") + passo) % len(frases)]
