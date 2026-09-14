"""Testes do registro de aceite e das ações do admin legal.

O AceiteLegal é prova, não cadastro: o que se verifica aqui é que a evidência
gravada é completa, que o admin não permite alterá-la e que a exportação em CSV
denuncia um documento adulterado.
"""

import csv
from io import StringIO

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase

from legal.admin import AceiteLegalAdmin, DocumentoLegalAdmin
from legal.models import (
    AceiteLegal,
    DocumentoLegal,
    OrigemAceite,
    StatusDocumento,
    TipoDocumento,
)
from legal.services import (
    SESSAO_ACEITE,
    aceite_anonimo_valido,
    documento_publicado,
    documentos_pendentes,
    historico,
    registrar_aceite,
)

Usuario = get_user_model()


def request_de_admin(user):
    """Request cru com sessão e storage de messages (o que o admin espera)."""
    request = RequestFactory().get("/admin/")
    request.user = user
    request.session = SessionStore()
    request._messages = FallbackStorage(request)
    return request


def criar_documento(tipo=TipoDocumento.TERMOS, versao="1.0", *, publicar=True, material=True):
    doc = DocumentoLegal.objects.create(
        tipo=tipo,
        versao=versao,
        titulo=f"Doc {tipo} {versao}",
        corpo_md="# Título\n\nTexto.",
        material=material,
    )
    if publicar:
        doc.publicar()
    return doc


class RegistrarAceiteTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.termos = criar_documento()
        self.privacidade = criar_documento(tipo=TipoDocumento.PRIVACIDADE)
        self.user = Usuario.objects.create_user("ana", email="ana@e.com", password="x")

    def _request(self, **extra):
        request = self.factory.post("/aceitar/", **extra)
        request.session = SessionStore()
        request.user = self.user
        return request

    def test_grava_um_registro_por_documento_vigente(self):
        request = self._request(HTTP_USER_AGENT="Mozilla/5.0")
        registros = registrar_aceite(request, origem=OrigemAceite.CADASTRO)

        self.assertEqual(len(registros), 2)
        aceite = AceiteLegal.objects.get(documento=self.termos)
        self.assertEqual(aceite.usuario, self.user)
        self.assertEqual(aceite.usuario_label, "ana@e.com")  # e-mail tem precedência
        self.assertEqual(aceite.documento_sha256, self.termos.sha256)
        self.assertTrue(aceite.integro)

    def test_evidencia_guarda_o_contexto_da_requisicao(self):
        request = self._request(HTTP_REFERER="https://origem/", HTTP_ACCEPT_LANGUAGE="pt-BR")
        registrar_aceite(request, origem=OrigemAceite.CADASTRO)

        evidencia = AceiteLegal.objects.first().evidencia
        self.assertEqual(evidencia["metodo"], "POST")
        self.assertEqual(evidencia["referer"], "https://origem/")
        self.assertEqual(evidencia["accept_language"], "pt-BR")
        self.assertEqual(evidencia["versoes_vigentes"]["termos"]["versao"], "1.0")

    def test_usuario_sem_e_mail_e_rotulado_pelo_username(self):
        self.user.email = ""
        self.user.save(update_fields=["email"])
        registrar_aceite(self._request(), origem=OrigemAceite.CADASTRO)
        self.assertEqual(AceiteLegal.objects.first().usuario_label, "ana")

    def test_anonimo_fica_registrado_na_sessao(self):
        # Sem conta a que vincular, o aceite vive no mesmo escopo dos dados dele.
        request = self._request()
        request.user = None

        registrar_aceite(request, origem=OrigemAceite.VISITANTE)

        gravados = set(request.session[SESSAO_ACEITE])
        self.assertEqual(gravados, {self.termos.pk, self.privacidade.pk})
        self.assertEqual(AceiteLegal.objects.first().usuario_label, "")
        self.assertTrue(aceite_anonimo_valido(request))

    def test_sessao_sem_todas_as_versoes_nao_vale(self):
        request = self._request()
        request.session[SESSAO_ACEITE] = [self.termos.pk]
        self.assertFalse(aceite_anonimo_valido(request))

    def test_sem_documento_publicado_qualquer_sessao_vale(self):
        DocumentoLegal.objects.all().delete()
        self.assertTrue(aceite_anonimo_valido(self._request()))

    def test_documentos_explicitos_restringem_o_registro(self):
        registrar_aceite(
            self._request(), origem=OrigemAceite.REACEITE, documentos=[self.privacidade]
        )
        self.assertEqual(
            list(AceiteLegal.objects.values_list("documento_id", flat=True)),
            [self.privacidade.pk],
        )

    def test_lista_vazia_de_documentos_nao_grava_nada(self):
        self.assertEqual(
            registrar_aceite(self._request(), origem=OrigemAceite.CADASTRO, documentos=[]), []
        )
        self.assertFalse(AceiteLegal.objects.exists())

    def test_sessao_e_criada_para_guardar_a_chave_na_prova(self):
        request = self._request()
        registrar_aceite(request, origem=OrigemAceite.CADASTRO)
        self.assertTrue(AceiteLegal.objects.first().session_key)


class DocumentosPendentesTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user("bob", password="x")

    def test_sem_documento_publicado_ninguem_deve_nada(self):
        self.assertEqual(documentos_pendentes(self.user), [])

    def test_versao_nao_material_nao_entra_na_pendencia(self):
        criar_documento(material=False)
        self.assertEqual(documentos_pendentes(self.user), [])

    def test_anonimo_deve_todos_os_materiais(self):
        criar_documento()
        self.assertEqual(len(documentos_pendentes(None)), 1)

    def test_quem_ja_aceitou_nao_deve_mais(self):
        doc = criar_documento()
        AceiteLegal.objects.create(
            documento=doc,
            usuario=self.user,
            origem=OrigemAceite.CADASTRO,
            documento_sha256=doc.sha256,
        )
        self.assertEqual(documentos_pendentes(self.user), [])


class ConsultaTests(TestCase):
    def test_documento_publicado_devolve_a_versao_vigente(self):
        criar_documento(versao="1.0")
        nova = criar_documento(versao="2.0")
        self.assertEqual(documento_publicado(TipoDocumento.TERMOS), nova)

    def test_sem_publicacao_devolve_none(self):
        criar_documento(publicar=False)
        self.assertIsNone(documento_publicado(TipoDocumento.TERMOS))

    def test_historico_lista_publicadas_e_arquivadas_da_mais_nova(self):
        criar_documento(versao="1.0")  # vira arquivada ao publicar a 2.0
        criar_documento(versao="2.0")
        criar_documento(versao="3.0", publicar=False)  # rascunho fica de fora

        versoes = [d.versao for d in historico(TipoDocumento.TERMOS)]
        self.assertEqual(versoes, ["2.0", "1.0"])


class DocumentoLegalAdminTests(TestCase):
    def setUp(self):
        self.admin = DocumentoLegalAdmin(DocumentoLegal, AdminSite())
        self.request = request_de_admin(Usuario.objects.create_superuser("root", password="x"))

    def test_previa_de_rascunho_sem_pk(self):
        self.assertIn("Salve o rascunho", self.admin.previa(DocumentoLegal()))

    def test_previa_de_rascunho_renderiza_o_markdown(self):
        doc = criar_documento(publicar=False)
        self.assertIn("<h1", self.admin.previa(doc))

    def test_previa_de_publicado_usa_o_html_congelado(self):
        doc = criar_documento()
        self.assertIn(doc.corpo_html, self.admin.previa(doc))

    def test_qtd_aceites_conta_os_registros(self):
        doc = criar_documento()
        AceiteLegal.objects.create(
            documento=doc, origem=OrigemAceite.VISITANTE, documento_sha256=doc.sha256
        )
        self.assertEqual(self.admin.qtd_aceites(doc), 1)

    def test_publicar_selecionados_so_afeta_rascunhos(self):
        rascunho = criar_documento(versao="1.0", publicar=False)
        publicado = criar_documento(tipo=TipoDocumento.PRIVACIDADE, versao="1.0")

        self.admin.publicar_selecionados(
            self.request, DocumentoLegal.objects.filter(pk__in=[rascunho.pk, publicado.pk])
        )

        rascunho.refresh_from_db()
        self.assertEqual(rascunho.status, StatusDocumento.PUBLICADO)

    def test_publicar_sem_rascunho_na_selecao_avisa(self):
        criar_documento()
        self.admin.publicar_selecionados(self.request, DocumentoLegal.objects.all())

        avisos = [str(m) for m in self.request._messages]
        self.assertIn("Nenhum rascunho na seleção.", avisos)
        # Nada mudou: o publicado continua publicado, sem nova versão.
        self.assertEqual(DocumentoLegal.objects.count(), 1)

    def test_duplicar_pula_versoes_ja_existentes(self):
        base = criar_documento(versao="1.0")
        criar_documento(versao="1.1", publicar=False)  # a próxima natural já existe

        self.admin.duplicar_como_nova_versao(
            self.request, DocumentoLegal.objects.filter(pk=base.pk)
        )

        versoes = set(DocumentoLegal.objects.values_list("versao", flat=True))
        self.assertIn("1.2", versoes)

    def test_duplicata_nasce_rascunho_e_material(self):
        base = criar_documento(material=False)
        self.admin.duplicar_como_nova_versao(
            self.request, DocumentoLegal.objects.filter(pk=base.pk)
        )
        nova = DocumentoLegal.objects.exclude(pk=base.pk).get()
        self.assertEqual(nova.status, StatusDocumento.RASCUNHO)
        self.assertTrue(nova.material)


class AceiteLegalAdminTests(TestCase):
    def setUp(self):
        self.admin = AceiteLegalAdmin(AceiteLegal, AdminSite())
        self.request = request_de_admin(Usuario.objects.create_superuser("root2", password="x"))
        self.doc = criar_documento()
        self.aceite = AceiteLegal.objects.create(
            documento=self.doc,
            usuario_label="ana@e.com",
            origem=OrigemAceite.CADASTRO,
            documento_sha256=self.doc.sha256,
            ip="203.0.113.9",
            session_key="abc123",
            user_agent="Mozilla/5.0",
        )

    def test_prova_nao_se_cria_altera_nem_apaga_pelo_admin(self):
        self.assertFalse(self.admin.has_add_permission(self.request))
        self.assertFalse(self.admin.has_change_permission(self.request, self.aceite))
        self.assertFalse(self.admin.has_delete_permission(self.request, self.aceite))

    def test_integridade_acusa_documento_divergente(self):
        self.assertTrue(self.admin.integridade(self.aceite))

        AceiteLegal.objects.filter(pk=self.aceite.pk).update(documento_sha256="outro-hash")
        self.aceite.refresh_from_db()
        self.assertFalse(self.admin.integridade(self.aceite))

    def test_exportar_csv_traz_a_linha_completa(self):
        resposta = self.admin.exportar_csv(self.request, AceiteLegal.objects.all())

        self.assertIn("text/csv", resposta["Content-Type"])
        self.assertIn("aceites.csv", resposta["Content-Disposition"])
        linhas = list(csv.reader(StringIO(resposta.content.decode())))
        self.assertEqual(linhas[0][:3], ["aceito_em", "usuario", "e_visitante"])

        registro = dict(zip(linhas[0], linhas[1], strict=True))
        self.assertEqual(registro["usuario"], "ana@e.com")
        self.assertEqual(registro["e_visitante"], "não")
        self.assertEqual(registro["integro"], "sim")
        self.assertEqual(registro["ip"], "203.0.113.9")

    def test_exportar_csv_marca_aceite_adulterado(self):
        AceiteLegal.objects.filter(pk=self.aceite.pk).update(documento_sha256="hash-que-nao-bate")
        resposta = self.admin.exportar_csv(self.request, AceiteLegal.objects.all())

        linhas = list(csv.reader(StringIO(resposta.content.decode())))
        registro = dict(zip(linhas[0], linhas[1], strict=True))
        self.assertEqual(registro["integro"], "NÃO")


class ImutabilidadeNoAdminTests(TestCase):
    """O admin é a fonte da verdade do texto legal — a trava é aqui, não na disciplina."""

    def setUp(self):
        self.admin = DocumentoLegalAdmin(DocumentoLegal, AdminSite())
        self.request = request_de_admin(Usuario.objects.create_superuser("root3", password="x"))

    def test_arquivado_tambem_e_intocavel(self):
        criar_documento(versao="1.0")
        criar_documento(versao="2.0")  # arquiva a 1.0
        arquivada = DocumentoLegal.objects.get(versao="1.0")

        self.assertEqual(arquivada.status, StatusDocumento.ARQUIVADO)
        self.assertFalse(self.admin.has_change_permission(self.request, arquivada))
        self.assertFalse(self.admin.has_delete_permission(self.request, arquivada))
        self.assertIn("corpo_md", self.admin.get_readonly_fields(self.request, arquivada))

    def test_rascunho_com_aceite_nao_pode_ser_apagado(self):
        rascunho = criar_documento(publicar=False)
        AceiteLegal.objects.create(
            documento=rascunho, origem=OrigemAceite.VISITANTE, documento_sha256=rascunho.sha256
        )
        self.assertFalse(self.admin.has_delete_permission(self.request, rascunho))

    def test_readonly_nao_tem_campo_repetido(self):
        publicado = criar_documento()
        campos = self.admin.get_readonly_fields(self.request, publicado)
        self.assertEqual(len(campos), len(set(campos)))
