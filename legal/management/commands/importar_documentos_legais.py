"""Cria no banco os documentos que ainda não existem, a partir dos arquivos .md.

Serve para o seed inicial e para levar ao ar um texto revisado em editor de código.
Nunca sobrescreve uma versão já existente: se o arquivo mudou, é preciso criar uma
versão nova — é o que impede alterar retroativamente um texto já aceito.
"""

from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from legal import documentos_io
from legal.models import DocumentoLegal, StatusDocumento, TipoDocumento
from legal.utils import calcular_sha256


class Command(BaseCommand):
    help = "Importa legal/documentos/<tipo>/<versao>.md para o banco."

    def add_arguments(self, parser):
        parser.add_argument(
            "--publicar",
            action="store_true",
            help="Publica imediatamente o que for criado (padrão: cria como rascunho).",
        )

    @transaction.atomic
    def handle(self, *args, **opcoes):
        arquivos = documentos_io.listar()
        if not arquivos:
            self.stdout.write(self.style.WARNING("Nenhum arquivo em legal/documentos/."))
            return

        criados = divergentes = ignorados = 0

        for tipo, versao, arquivo in arquivos:
            if tipo not in TipoDocumento.values:
                self.stderr.write(f"tipo desconhecido, pulando: {arquivo}")
                continue

            metadados, corpo = documentos_io.ler(arquivo)
            existente = DocumentoLegal.objects.filter(tipo=tipo, versao=versao).first()

            if existente is not None:
                ignorados += 1
                # Aviso, não erro: rascunho ainda pode divergir legitimamente.
                if existente.sha256 and existente.sha256 != calcular_sha256(corpo):
                    divergentes += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f"{tipo} v{versao}: o arquivo difere do texto publicado. "
                            "Crie uma versão nova em vez de alterar esta."
                        )
                    )
                continue

            documento = DocumentoLegal(
                tipo=tipo,
                versao=versao,
                titulo=metadados.get("titulo") or TipoDocumento(tipo).label,
                corpo_md=corpo,
                material=(metadados.get("material", "true").lower() != "false"),
                status=StatusDocumento.RASCUNHO,
            )
            data = parse_date(metadados.get("vigente_desde", "") or "")
            if data:
                documento.vigente_desde = timezone.make_aware(
                    datetime.combine(data, datetime.min.time())
                )
            documento.save()
            criados += 1

            if opcoes["publicar"]:
                documento.publicar()

            self.stdout.write(
                self.style.SUCCESS(
                    f"criado {tipo} v{versao}"
                    + (" (publicado)" if opcoes["publicar"] else " (rascunho)")
                )
            )

        self.stdout.write(
            f"{criados} criado(s), {ignorados} já existente(s), {divergentes} divergente(s)."
        )
        if divergentes:
            raise SystemExit(1)
