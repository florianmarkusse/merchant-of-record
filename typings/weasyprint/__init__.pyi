# The part of WeasyPrint's API we call (src/mor/web/invoice_pdf.py, and the
# fake carrier's PDFs in src/mor/fakes/carrier.py): it ships no types, and
# pyright is strict. Checked against WeasyPrint 70.0.

from collections.abc import Sequence

from weasyprint.urls import URLFetcher

class CSS:
    def __init__(self, *, string: str, url_fetcher: URLFetcher) -> None: ...

class HTML:
    def __init__(self, *, string: str, url_fetcher: URLFetcher) -> None: ...
    def write_pdf(self, *, stylesheets: Sequence[CSS]) -> bytes: ...
