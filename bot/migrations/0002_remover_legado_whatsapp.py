"""Remove as tabelas do app `zap`, que atendia o WhatsApp.

Por que não é um rename de tabela: o que aquelas tabelas guardavam não tem
equivalente aqui. A identidade em `zap_numerowhatsapp` era um telefone em
E.164, e a deste app é o `chat.id` do Telegram, um inteiro que o Telegram
atribui e que não se deriva de número nenhum. Renomear a tabela produziria
linhas com um `chat_id` inventado, para as quais todo envio falharia.

Então o vínculo foi refeito pelo portal, o que no Telegram é um toque. O
histórico de mensagens do WhatsApp sai junto: é log de transporte de um canal
que deixou de existir, e os lançamentos que ele gerou estão em `carteira`,
intactos.

`IF EXISTS` porque em banco novo (a suíte, um ambiente recém-criado) não há
nada disso para remover.
"""

from django.db import migrations

# Filhas antes das mães: o SQLite não tem CASCADE no DROP TABLE.
TABELAS_LEGADAS = [
    "zap_midia",
    "zap_mensagem",
    "zap_janelaatendimento",
    "zap_codigopareamento",
    "zap_numerowhatsapp",
]


def remover(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for tabela in TABELAS_LEGADAS:
            cursor.execute(f'DROP TABLE IF EXISTS "{tabela}"')
        # Sem isto o Django continua achando que o app `zap` está migrado e,
        # num ambiente recriado a partir deste banco, o histórico não bate.
        cursor.execute("DELETE FROM django_migrations WHERE app = 'zap'")


class Migration(migrations.Migration):
    dependencies = [("bot", "0001_initial")]

    operations = [
        # Sem reverso: recriar tabelas vazias não devolveria dado nenhum, e
        # fingir que devolve é pior do que assumir que a volta é pelo backup.
        migrations.RunPython(remover, migrations.RunPython.noop),
    ]
