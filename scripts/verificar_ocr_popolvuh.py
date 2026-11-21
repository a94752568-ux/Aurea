"""Verificación OCR REAL — Fase 2 preliminar (Popol Vuh de Alberto).

Procesa SOLO las páginas 1-10 del PDF (el libro tiene 183; NO se
procesa entero). Reporta los números Fase 2 sobre esas 10 páginas y
estima el tiempo del libro completo a partir del ritmo medido.

Uso:
    python scripts/verificar_ocr_popolvuh.py [ruta.pdf] [--pages N]
    (por defecto: ~/workspace/user/files/Popol_Vuh_0_w82v.pdf, 10 págs.)

Escribe el reporte en output/ocr_popolvuh_muestra_reporte.md
(book_id "popolvuh-muestra"). La caché es la estándar de AUREA
(AUREA_HOME/ocr_cache): una segunda corrida reutiliza las páginas
ya procesadas sin re-OCRear.

NO es un test pytest: el PDF real no pertenece al repo.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurea.ingestion.ocr_reader import (  # noqa: E402
    read_pdf_ocr, write_ocr_report)

DEFAULT_PDF = (Path.home() / "workspace" / "user" / "files"
               / "Popol_Vuh_0_w82v.pdf")


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PDF
    n_pages = 10
    if "--pages" in argv:
        n_pages = int(argv[argv.index("--pages") + 1])
    if not path.exists():
        print(f"NO EXISTE: {path}")
        return 1
    import pymupdf
    total_doc = len(pymupdf.open(str(path)))
    print(f"PDF: {path} ({total_doc} páginas en total)")
    print(f"Muestra: páginas 1-{n_pages} (NO se procesa el libro entero)")
    t0 = time.time()
    per_page_s = []

    def progress(pg, total, method):
        print(f"  p{pg}/{total}: {method}", flush=True)

    chapters, report, pages = read_pdf_ocr(
        str(path), lang="spa+eng", dpi=300, progress_cb=progress,
        page_range=(1, n_pages))
    wall = time.time() - t0
    headers = [r for r in report.removed if r["motivo"] == "encabezado repetido"]
    footers = [r for r in report.removed if r["motivo"] == "pie repetido"]
    pagenums = [r for r in report.removed if r["motivo"] == "número de página"]
    two_col = [p.page_no for p in pages if p.columns == 2]
    psms = sorted({p.psm for p in pages if p.psm})
    print()
    print("=== NÚMEROS FASE 2 (muestra real, págs. 1-%d) ===" % n_pages)
    print(f"Páginas totales (muestra): {report.total_pages}")
    print(f"Páginas por OCR: {report.ocr_pages}")
    print(f"Páginas por texto: {report.text_pages}")
    print(f"Confianza media global: {report.mean_conf:.1f}")
    print(f"Páginas a revisar (<70): {report.review_pages or 'ninguna'}")
    print(f"Encabezados repetidos eliminados: {len(headers)}")
    for r in headers[:5]:
        print(f"  p{r['page']}: «{r['text'][:70]}»")
    print(f"Pies repetidos eliminados: {len(footers)}")
    print(f"Números de página eliminados: {len(pagenums)}")
    print(f"Palabras desguionadas: {report.dehyphenated}")
    print(f"Palabras de baja confianza (<40): {len(report.low_conf_words)}")
    for pg, ln, w in report.low_conf_words[:10]:
        print(f"  p{pg}, línea {ln}: «{w}»")
    print(f"Páginas a 2 columnas: {two_col or 'ninguna'}")
    print(f"PSM usados: {psms}")
    print(f"Llamadas OCR reales: {report.ocr_calls} "
          f"(caché: {report.cache_hits})")
    print(f"Segundos por página (media): {report.seconds_per_page:.2f}")
    print(f"Tiempo total muestra: {wall:.1f} s")
    est = report.seconds_per_page * total_doc
    print(f"ESTIMACIÓN libro completo ({total_doc} págs.): "
          f"{est / 60:.1f} min ({est:.0f} s)")
    print(f"Notas al pie conservadas: {len(report.footnotes_kept)}")
    # Muestra de texto (primeros 600 caracteres de la p1).
    print()
    print("--- Muestra de texto (p1, 600 car.) ---")
    print(pages[0].text[:600])
    out = write_ocr_report(report, "popolvuh-muestra",
                           Path(__file__).resolve().parent.parent
                           / "output")
    print()
    print(f"Reporte escrito en: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
