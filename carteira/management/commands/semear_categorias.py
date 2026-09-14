from django.core.management.base import BaseCommand

from accounts.models import Espaco
from carteira.seeds import semear_categorias


class Command(BaseCommand):
    help = "Cria as categorias padrão nos espaços que ainda não as têm."

    def add_arguments(self, parser):
        parser.add_argument("--espaco", type=int, help="ID de um espaço específico.")

    def handle(self, *args, **opcoes):
        espacos = Espaco.objects.all()
        if opcoes.get("espaco"):
            espacos = espacos.filter(pk=opcoes["espaco"])

        total = 0
        for espaco in espacos:
            criadas = semear_categorias(espaco)
            total += criadas
            self.stdout.write(f"{espaco}: {criadas} categoria(s) criada(s).")

        self.stdout.write(self.style.SUCCESS(f"{total} categoria(s) no total."))
