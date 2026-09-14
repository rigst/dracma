"""Escreve em legal/documentos/ os documentos publicados no banco.

O banco é a fonte da verdade; estes arquivos são o espelho versionado em git —
evidência redundante e diff legível de uma versão para a outra. Rode depois de
publicar uma versão nova pelo admin e faça commit do resultado.
"""

from django.core.management.base import BaseCommand

from legal import documentos_io
from legal.models import DocumentoLegal, StatusDocumento


class Command(BaseCommand):
    help = "Exporta os documentos publicados/arquivados para legal/documentos/."

    def handle(self, *args, **opcoes):
        documentos = DocumentoLegal.objects.filter(
            status__in=[StatusDocumento.PUBLICADO, StatusDocumento.ARQUIVADO]
        ).order_by("tipo", "versao")

        if not documentos:
            self.stdout.write(self.style.WARNING("Nenhum documento publicado."))
            return

        for documento in documentos:
            metadados = {
                "titulo": documento.titulo,
                "material": "true" if documento.material else "false",
            }
            if documento.vigente_desde:
                metadados["vigente_desde"] = documento.vigente_desde.date().isoformat()
            metadados["sha256"] = documento.sha256
            destino = documentos_io.escrever(
                documento.tipo, documento.versao, metadados, documento.corpo_md
            )
            self.stdout.write(self.style.SUCCESS(f"escrito {destino}"))

        self.stdout.write(f"{documentos.count()} documento(s) exportado(s).")
