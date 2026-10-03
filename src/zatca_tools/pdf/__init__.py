"""Printed invoices: PDF/A-3 with the e-invoice's XML embedded, as ZATCA's guidelines describe for sharing.

The PDF is drawn from the XML — the one you signed, or ZATCA's cleared copy —
and that same XML is embedded in it (associated file, relationship
``Alternative``), so the human-readable invoice and the machine-readable one
travel as one file and cannot disagree.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ..design import InvoiceDesign
from ..errors import PdfError


def render(
    xml: str,
    design: Optional[InvoiceDesign] = None,
    *,
    items: Sequence[dict] = (),
    gross: Optional[float] = None,
    discount: Optional[float] = None,
    cleared: bool = False,
) -> bytes:
    """The PDF/A-3B bytes of the invoice ``xml``, with ``xml`` embedded.

    ``items``, ``gross`` and ``discount`` come from the Invoice when there is
    one: what the XML does not carry (an item's description, its agreed price
    and own discount; the total before an invoice discount). ``cleared`` says
    the XML is ZATCA's stamped copy, which the embedded file's description then
    states.
    """
    try:
        from . import layout
        from .reader import read
    except ImportError as missing:
        raise PdfError(f"The installation is incomplete ({missing.name} is missing); reinstall it: pip install --force-reinstall zatca-tools-sdk") from None
    try:
        printed = read(xml)
    except Exception as exc:  # noqa: BLE001 - any unreadable XML is the same problem for the caller
        raise PdfError(f"The invoice XML could not be read for printing: {exc}") from exc
    printed.items, printed.gross, printed.discount = [dict(item) for item in items], gross, discount
    try:
        return layout.render(printed, xml.encode("utf-8"), design or InvoiceDesign(), cleared=cleared)
    except PdfError:
        raise
    except OSError as exc:
        raise PdfError(f"The logo could not be read: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - surface layout failures as one typed error
        raise PdfError(f"The PDF could not be produced: {exc}") from exc


__all__ = ["render"]
