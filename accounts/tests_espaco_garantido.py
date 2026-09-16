"""Todo usuário nasce com espaço, venha de onde vier.

O espaço é onde moram contas, categorias, lançamentos e limites. Um usuário
sem espaço não é uma conta vazia: é uma conta quebrada. O portal abre sem
nada, e no Telegram a pessoa pareia e mesmo assim recebe o convite de
pareamento de novo a cada mensagem, porque "sem espaço" caía no mesmo ramo de
"não pareado".

Isso acontecia com quem nascia fora do fluxo de cadastro: criado no admin, por
`createsuperuser` ou pelo shell.
"""

from __future__ import annotations

from django.test import TestCase

from accounts.models import Espaco, Usuario
from carteira.models import Categoria


class NascimentoTest(TestCase):
    def test_criado_pelo_shell_ganha_espaco(self):
        usuario = Usuario.objects.create_user(
            username="leo", email="leo@exemplo.com", password="x"
        )
        self.assertIsNotNone(usuario.espaco)

    def test_o_espaco_ja_vem_com_categorias(self):
        """Sem elas, o primeiro lançamento cairia em 'sem categoria'."""
        usuario = Usuario.objects.create_user(
            username="leo", email="leo@exemplo.com", password="x"
        )
        self.assertTrue(Categoria.objects.filter(espaco=usuario.espaco).exists())

    def test_superusuario_tambem(self):
        usuario = Usuario.objects.create_superuser(
            username="chefe", email="chefe@exemplo.com", password="x"
        )
        self.assertIsNotNone(usuario.espaco)

    def test_quem_ja_tem_espaco_nao_ganha_outro(self):
        espaco = Espaco.objects.create(nome="Casa")
        usuario = Usuario.objects.create_user(
            username="ana", email="ana@exemplo.com", password="x", espaco=espaco
        )
        self.assertEqual(usuario.espaco, espaco)
        self.assertEqual(Espaco.objects.count(), 1)

    def test_salvar_de_novo_nao_cria_espaco_novo(self):
        usuario = Usuario.objects.create_user(
            username="leo", email="leo@exemplo.com", password="x"
        )
        primeiro = usuario.espaco_id
        usuario.first_name = "Leo"
        usuario.save()
        self.assertEqual(usuario.espaco_id, primeiro)
        self.assertEqual(Espaco.objects.count(), 1)


class UpdateFieldsTest(TestCase):
    def test_o_vinculo_e_gravado_mesmo_com_update_fields(self):
        """`update_fields` lista o que vai ao banco.

        Sem incluir "espaco" ali, o espaço seria criado e o vínculo não seria
        gravado: o usuário voltaria do banco sem espaço e com um órfão solto.
        """
        usuario = Usuario.objects.create_user(
            username="leo", email="leo@exemplo.com", password="x"
        )
        Usuario.objects.filter(pk=usuario.pk).update(espaco=None)

        recarregado = Usuario.objects.get(pk=usuario.pk)
        self.assertIsNone(recarregado.espaco_id)

        recarregado.first_name = "Leo"
        recarregado.save(update_fields=["first_name"])

        do_banco = Usuario.objects.get(pk=usuario.pk)
        self.assertIsNotNone(do_banco.espaco_id)
        self.assertEqual(do_banco.first_name, "Leo")
