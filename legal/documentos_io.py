"""Leitura e escrita dos arquivos `legal/documentos/<tipo>/<versao>.md`.

Formato do arquivo: um cabeçalho `chave: valor` por linha, um `---` isolado, e o
Markdown a seguir. Parser à mão de propósito — não vale uma dependência de YAML
para três chaves.
"""

from pathlib import Path

from .utils import normalizar_texto

DIRETORIO = Path(__file__).resolve().parent / "documentos"
SEPARADOR = "---"


def caminho(tipo, versao):
    return DIRETORIO / tipo / f"{versao}.md"


def ler(arquivo):
    """-> (metadados: dict, corpo: str)"""
    bruto = normalizar_texto(arquivo.read_text(encoding="utf-8"))
    metadados = {}
    linhas = bruto.split("\n")
    corte = 0
    for indice, linha in enumerate(linhas):
        if linha.strip() == SEPARADOR:
            corte = indice + 1
            break
        if ":" in linha:
            chave, valor = linha.split(":", 1)
            metadados[chave.strip()] = valor.strip()
    else:
        # Sem separador: o arquivo inteiro é corpo.
        return {}, bruto
    return metadados, normalizar_texto("\n".join(linhas[corte:]))


def escrever(tipo, versao, metadados, corpo):
    destino = caminho(tipo, versao)
    destino.parent.mkdir(parents=True, exist_ok=True)
    cabecalho = "\n".join(f"{chave}: {valor}" for chave, valor in metadados.items())
    destino.write_text(f"{cabecalho}\n{SEPARADOR}\n{normalizar_texto(corpo)}", encoding="utf-8")
    return destino


def listar():
    """-> [(tipo, versao, Path)] de todos os arquivos existentes."""
    encontrados: list[tuple[str, str, Path]] = []
    if not DIRETORIO.exists():
        return encontrados
    for pasta in sorted(p for p in DIRETORIO.iterdir() if p.is_dir()):
        for arquivo in sorted(pasta.glob("*.md")):
            encontrados.append((pasta.name, arquivo.stem, arquivo))
    return encontrados
