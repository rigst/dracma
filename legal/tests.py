from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.tasks import cleanup_expired_visitors

from . import documentos_io
from .forms import AceiteForm
from .models import AceiteLegal, DocumentoLegal, OrigemAceite, StatusDocumento, TipoDocumento
from .services import documentos_vigentes, precisa_reaceitar
from .utils import calcular_sha256, ip_do_request, renderizar_markdown

Usuario = get_user_model()


def criar_documento(
    tipo=TipoDocumento.TERMOS,
    versao="1.0",
    *,
    publicar=True,
    material=True,
    corpo="# Título\n\nTexto do documento.\n",
):
    documento = DocumentoLegal.objects.create(
        tipo=tipo, versao=versao, titulo=f"Doc {tipo}", corpo_md=corpo, material=material
    )
    if publicar:
        documento.publicar()
    return documento


class UtilsTests(TestCase):
    def test_ip_usa_ultimo_item_do_forwarded_for(self):
        """O último item é o que o nginx observou; os anteriores vêm do cliente."""
        request = self.client.request().wsgi_request
        request.META["HTTP_X_FORWARDED_FOR"] = "198.51.100.1, 203.0.113.9"
        self.assertEqual(ip_do_request(request), "203.0.113.9")

    def test_ip_ignora_forwarded_for_forjado_pelo_cliente(self):
        request = self.client.request().wsgi_request
        request.META["HTTP_X_FORWARDED_FOR"] = "1.2.3.4"
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        # Só há um item: é o que o nginx acrescentou.
        self.assertEqual(ip_do_request(request), "1.2.3.4")

    def test_markdown_e_sanitizado(self):
        html = renderizar_markdown("Texto <script>alert(1)</script> normal.")
        self.assertNotIn("<script>", html)
        self.assertNotIn("alert(1)", html)

    def test_hash_ignora_quebra_de_linha_e_espaco_final(self):
        self.assertEqual(calcular_sha256("a\r\nb  \n"), calcular_sha256("a\nb\n"))


class DocumentoLegalTests(TestCase):
    def test_publicar_congela_html_e_hash(self):
        documento = criar_documento()
        self.assertEqual(documento.status, StatusDocumento.PUBLICADO)
        self.assertEqual(documento.sha256, calcular_sha256(documento.corpo_md))
        self.assertIn("<h1>Título</h1>", documento.corpo_html)

    def test_publicar_arquiva_versao_anterior_do_mesmo_tipo(self):
        antiga = criar_documento(versao="1.0")
        criar_documento(versao="1.1")
        antiga.refresh_from_db()
        self.assertEqual(antiga.status, StatusDocumento.ARQUIVADO)

    def test_vigente_retorna_a_ultima_publicada(self):
        criar_documento(versao="1.0")
        nova = criar_documento(versao="1.1")
        self.assertEqual(DocumentoLegal.objects.vigente(TipoDocumento.TERMOS), nova)


class AceiteFormTests(TestCase):
    def test_rejeita_sem_marcar(self):
        form = AceiteForm(data={})
        self.assertFalse(form.is_valid())
        self.assertIn("aceite_legal", form.errors)

    def test_aceita_marcado(self):
        self.assertTrue(AceiteForm(data={"aceite_legal": "on"}).is_valid())

    def test_campo_nunca_nasce_marcado(self):
        import re

        criar_documento()
        criar_documento(tipo=TipoDocumento.PRIVACIDADE)
        # O aceite saiu do login para uma página própria.
        html = self.client.get(reverse("legal:aceite_visitante")).content.decode()

        tags = re.findall(r"<input[^>]*name=\"aceite_legal\"[^>]*>", html)
        self.assertEqual(len(tags), 1, "esperava exatamente um checkbox de aceite")
        self.assertNotIn("checked", tags[0])
        self.assertIn("required", tags[0])

    def test_template_nao_vaza_comentario_de_desenvolvimento(self):
        """`{# #}` no Django é de uma linha só: comentário em bloco vaza para a página."""
        criar_documento()
        # O aceite saiu do login para uma página própria.
        html = self.client.get(reverse("legal:aceite_visitante")).content.decode()
        self.assertNotIn("initial=False", html)


class AceiteVisitanteTests(TestCase):
    def setUp(self):
        self.termos = criar_documento()
        self.privacidade = criar_documento(tipo=TipoDocumento.PRIVACIDADE)

    def test_visitante_sem_aceite_nao_cria_conta(self):
        resposta = self.client.post(reverse("accounts:entrar_visitante"), {})

        # Re-renderiza a própria tela de aceite com o erro no campo.
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "É preciso aceitar")
        self.assertFalse(Usuario.objects.filter(username__startswith="visitante_").exists())
        self.assertFalse(AceiteLegal.objects.exists())

    def test_visitante_com_aceite_registra_prova_completa(self):
        resposta = self.client.post(
            reverse("accounts:entrar_visitante"),
            {"aceite_legal": "on"},
            HTTP_X_FORWARDED_FOR="198.51.100.1, 203.0.113.9",
            HTTP_USER_AGENT="Mozilla/5.0 (Teste)",
        )

        self.assertRedirects(resposta, reverse("dashboard"))
        visitante = Usuario.objects.get(username__startswith="visitante_")

        aceites = AceiteLegal.objects.filter(usuario=visitante)
        self.assertEqual(aceites.count(), 2)  # termos + privacidade

        aceite = aceites.get(documento=self.termos)
        self.assertEqual(aceite.ip, "203.0.113.9")
        self.assertEqual(aceite.user_agent, "Mozilla/5.0 (Teste)")
        self.assertEqual(aceite.origem, OrigemAceite.VISITANTE)
        self.assertTrue(aceite.e_visitante)
        self.assertEqual(aceite.usuario_label, visitante.username)
        self.assertEqual(aceite.documento_sha256, self.termos.sha256)
        self.assertTrue(aceite.integro)
        self.assertTrue(aceite.session_key)
        self.assertEqual(aceite.evidencia["versoes_vigentes"]["termos"]["versao"], "1.0")

    def test_prova_sobrevive_a_exclusao_do_visitante(self):
        """O ponto mais delicado: visitante é apagado ao expirar, a prova não pode ir junto."""
        self.client.post(reverse("accounts:entrar_visitante"), {"aceite_legal": "on"})
        visitante = Usuario.objects.get(username__startswith="visitante_")
        rotulo = visitante.username

        # Expira o visitante e roda a limpeza real (queryset.delete()).
        visitante.profile.expires_at = timezone.now() - timezone.timedelta(hours=1)
        visitante.profile.save(update_fields=["expires_at"])
        cleanup_expired_visitors()

        self.assertFalse(Usuario.objects.filter(username=rotulo).exists())
        aceites = AceiteLegal.objects.all()
        self.assertEqual(aceites.count(), 2)
        for aceite in aceites:
            self.assertIsNone(aceite.usuario)
            self.assertEqual(aceite.usuario_label, rotulo)
            self.assertTrue(aceite.e_visitante)


class AceiteCadastroTests(TestCase):
    """O cadastro aqui passa por confirmação de e-mail: o aceite é dado no
    formulário, mas só vira registro quando a conta é ativada."""

    def setUp(self):
        self.termos = criar_documento()
        self.privacidade = criar_documento(tipo=TipoDocumento.PRIVACIDADE)

    def _dados(self, **extra):
        dados = {
            "username": "novo_estudante",
            "email": "novo@example.com",
            "password1": "senha-bem-forte-123",
            "password2": "senha-bem-forte-123",
        }
        dados.update(extra)
        return dados

    @override_settings(SIGNUP_ENABLED=True)
    def test_cadastro_sem_aceite_e_recusado(self):
        resposta = self.client.post(reverse("accounts:cadastro"), self._dados())

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Usuario.objects.filter(username="novo_estudante").exists())

    @override_settings(SIGNUP_ENABLED=True)
    def test_aceite_do_cadastro_so_e_gravado_na_confirmacao(self):
        self.client.post(reverse("accounts:cadastro"), self._dados(aceite_legal="on"))
        user = Usuario.objects.get(username="novo_estudante")

        # Conta criada e inativa: ainda não há aceite a registrar.
        self.assertFalse(user.is_active)
        self.assertFalse(AceiteLegal.objects.exists())

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        self.client.get(reverse("accounts:confirmar_email", args=[uid, token]))

        user.refresh_from_db()
        self.assertTrue(user.is_active)
        aceites = AceiteLegal.objects.filter(usuario=user)
        self.assertEqual(aceites.count(), 2)
        self.assertEqual(aceites.first().origem, OrigemAceite.CADASTRO)


class ReaceiteMiddlewareTests(TestCase):
    def setUp(self):
        self.termos = criar_documento()
        self.usuario = Usuario.objects.create_user(username="ana", password="senha-forte-123")
        self.client.force_login(self.usuario)

    def test_conta_sem_aceite_e_barrada(self):
        """Contas anteriores ao app caem no interstitial — backfill sem migração."""
        resposta = self.client.get(reverse("dashboard"))
        self.assertRedirects(resposta, reverse("legal:reaceite"))

    def test_rotas_da_allowlist_seguem_acessiveis(self):
        for rota in (reverse("termos"), reverse("legal:reaceite")):
            self.assertEqual(self.client.get(rota).status_code, 200)

    def test_aceitar_libera_a_navegacao(self):
        self.client.post(reverse("legal:reaceite"), {"aceite_legal": "on"})
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        self.assertTrue(AceiteLegal.objects.filter(usuario=self.usuario).exists())

    def test_recusar_mantem_bloqueio(self):
        resposta = self.client.post(reverse("legal:reaceite"), {})
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(AceiteLegal.objects.exists())

    def test_versao_material_nova_exige_reaceite(self):
        self.client.post(reverse("legal:reaceite"), {"aceite_legal": "on"})
        self.assertFalse(precisa_reaceitar(self.usuario))

        criar_documento(versao="2.0")

        self.assertTrue(precisa_reaceitar(self.usuario))
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("legal:reaceite"))

    def test_versao_nao_material_nao_exige_reaceite(self):
        self.client.post(reverse("legal:reaceite"), {"aceite_legal": "on"})
        criar_documento(versao="1.1", material=False)
        self.assertFalse(precisa_reaceitar(self.usuario))

    def test_reaceite_gera_registro_da_nova_versao_preservando_o_antigo(self):
        self.client.post(reverse("legal:reaceite"), {"aceite_legal": "on"})
        nova = criar_documento(versao="2.0")
        self.client.post(reverse("legal:reaceite"), {"aceite_legal": "on"})

        aceites = AceiteLegal.objects.filter(usuario=self.usuario)
        self.assertEqual(aceites.count(), 2)
        self.assertTrue(aceites.filter(documento=self.termos).exists())
        self.assertTrue(aceites.filter(documento=nova, origem=OrigemAceite.REACEITE).exists())


class PaginasPublicasTests(TestCase):
    def test_termos_e_privacidade_saem_do_banco(self):
        criar_documento(corpo="# Termos\n\nConteúdo publicado.\n")
        criar_documento(tipo=TipoDocumento.PRIVACIDADE, corpo="# Privacidade\n\nOutro texto.\n")

        self.assertContains(self.client.get(reverse("termos")), "Conteúdo publicado.")
        self.assertContains(self.client.get(reverse("privacidade")), "Outro texto.")

    def test_sem_versao_publicada_responde_404(self):
        self.assertEqual(self.client.get(reverse("termos")).status_code, 404)

    def test_versao_arquivada_continua_consultavel(self):
        antiga = criar_documento(versao="1.0", corpo="# V1\n\nTexto antigo.\n")
        criar_documento(versao="2.0", corpo="# V2\n\nTexto novo.\n")

        resposta = self.client.get(reverse("legal:versao", args=[antiga.tipo, antiga.versao]))
        self.assertContains(resposta, "Texto antigo.")
        self.assertContains(resposta, "versão anterior")

    def test_rascunho_nao_e_publico(self):
        rascunho = criar_documento(versao="9.0", publicar=False)
        resposta = self.client.get(reverse("legal:versao", args=[rascunho.tipo, rascunho.versao]))
        self.assertEqual(resposta.status_code, 404)


class AdminImutabilidadeTests(TestCase):
    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from .admin import DocumentoLegalAdmin

        self.admin = DocumentoLegalAdmin(DocumentoLegal, AdminSite())
        self.request = self.client.request().wsgi_request
        self.request.user = Usuario.objects.create_superuser(
            username="root", password="senha-forte-123"
        )

    def test_rascunho_e_editavel(self):
        rascunho = criar_documento(publicar=False)
        self.assertTrue(self.admin.has_change_permission(self.request, rascunho))
        self.assertTrue(self.admin.has_delete_permission(self.request, rascunho))

    def test_publicado_nao_e_editavel_nem_apagavel(self):
        documento = criar_documento()
        self.assertFalse(self.admin.has_change_permission(self.request, documento))
        self.assertFalse(self.admin.has_delete_permission(self.request, documento))

    def test_campos_ficam_somente_leitura_apos_publicar(self):
        documento = criar_documento()
        readonly = self.admin.get_readonly_fields(self.request, documento)
        for campo in ("corpo_md", "versao", "tipo", "material"):
            self.assertIn(campo, readonly)

    def test_aceite_e_somente_leitura(self):
        from django.contrib.admin.sites import AdminSite

        from .admin import AceiteLegalAdmin

        aceite_admin = AceiteLegalAdmin(AceiteLegal, AdminSite())
        self.assertFalse(aceite_admin.has_add_permission(self.request))
        self.assertFalse(aceite_admin.has_change_permission(self.request))
        self.assertFalse(aceite_admin.has_delete_permission(self.request))

    def test_duplicar_cria_rascunho_com_versao_nova(self):
        documento = criar_documento(versao="1.0")
        self.admin.duplicar_como_nova_versao(
            self.request, DocumentoLegal.objects.filter(pk=documento.pk)
        )
        nova = DocumentoLegal.objects.get(tipo=documento.tipo, versao="1.1")
        self.assertEqual(nova.status, StatusDocumento.RASCUNHO)
        self.assertEqual(nova.corpo_md, documento.corpo_md)


class CommandsTests(TestCase):
    def test_exportar_reproduz_o_texto_publicado(self):
        from django.core.management import call_command

        corpo = "# Termos\n\nTexto **exato** publicado.\n"
        documento = criar_documento(versao="7.7", corpo=corpo)
        call_command("exportar_documentos_legais", verbosity=0)

        arquivo = documentos_io.caminho(documento.tipo, documento.versao)
        self.addCleanup(arquivo.unlink, missing_ok=True)

        _metadados, corpo_lido = documentos_io.ler(arquivo)
        self.assertEqual(calcular_sha256(corpo_lido), documento.sha256)

    def test_importar_nao_sobrescreve_versao_existente(self):
        from django.core.management import call_command

        documento = criar_documento(versao="8.8", corpo="# Original\n\nTexto.\n")
        arquivo = documentos_io.escrever(
            documento.tipo,
            documento.versao,
            {"titulo": "Adulterado"},
            "# Outro\n\nTexto trocado.\n",
        )
        self.addCleanup(arquivo.unlink, missing_ok=True)

        with self.assertRaises(SystemExit):
            call_command("importar_documentos_legais", verbosity=0)

        documento.refresh_from_db()
        self.assertIn("Original", documento.corpo_md)


class ServicesTests(TestCase):
    def test_documentos_vigentes_traz_um_por_tipo(self):
        criar_documento(versao="1.0")
        criar_documento(versao="2.0")
        criar_documento(tipo=TipoDocumento.PRIVACIDADE)

        vigentes = documentos_vigentes()
        self.assertEqual(set(vigentes), {"termos", "privacidade"})
        self.assertEqual(vigentes["termos"].versao, "2.0")

    def test_sem_documento_publicado_ninguem_e_barrado(self):
        usuario = Usuario.objects.create_user(username="bia", password="senha-forte-123")
        self.assertFalse(precisa_reaceitar(usuario))
