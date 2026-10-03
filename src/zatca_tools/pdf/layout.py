"""The default invoice layout: A4, Arabic first with English beside it, PDF/A-3B with the XML inside.

What is printed follows the VAT Implementing Regulations (art. 53) and ZATCA's
e-invoicing rules: the document's title, number, date (and time on a simplified
invoice), the seller's name, address and VAT number, the buyer's on a standard
invoice, each item with its quantity and unit price, the exemption's reason,
the taxable amount, the VAT and the total with VAT, and the QR code.

It follows the ZATCA Tools platform's printed invoice, rule for rule: every
row reads across — before VAT, the VAT, with VAT (founder, 2026-09-16); the
rate sits in the VAT header when the invoice has one, and gets its own column
only when rates differ (2026-09-26); on an invoice that mixes treatments the
exemption reason is printed under the item it explains, otherwise once as a
note (2026-09-27); an invoice discount shows the total before it, then the
amount off; the total due says it includes VAT (Annex 2, 8.5).
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from importlib import resources
from typing import Optional

import segno
from fpdf import FPDF
from fpdf.enums import OutputIntentSubType
from fpdf.output import PDFICCProfile
from PIL import Image, ImageCms

from ..design import InvoiceDesign
from .reader import Party, PrintedInvoice

INK = (16, 22, 19)
MUTED = (98, 108, 102)
RULE = (220, 226, 223)
WHITE = (255, 255, 255)

PAGE_W, PAGE_H = 210.0, 297.0
MARGIN = 14.0
CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_TOP = PAGE_H - 22.0

#: A figure with its percent sign. Inside Arabic text the bidi algorithm throws
#: the "%" to the wrong side ("%15"); an LTR isolate keeps "15%" whole.
_ARABIC = re.compile("[\u0600-\u06ff]")
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s?%")

TITLES = {
    ("invoice", False): ("فاتورة ضريبية", "Tax Invoice"),
    ("invoice", True): ("فاتورة ضريبية مبسطة", "Simplified Tax Invoice"),
    ("credit", False): ("إشعار دائن", "Credit Note"),
    ("credit", True): ("إشعار دائن", "Credit Note"),
    ("debit", False): ("إشعار مدين", "Debit Note"),
    ("debit", True): ("إشعار مدين", "Debit Note"),
}

CATEGORIES = {
    "S": ("خاضع للضريبة", "Standard rated"),
    "Z": ("خاضع لنسبة الصفر", "Zero-rated"),
    "E": ("معفى من الضريبة", "Exempt"),
    "O": ("خارج نطاق الضريبة", "Out of scope"),
}

#: ZATCA's exemption reasons in Arabic, as the platform prints them; the English is the Authority's own text, from the XML.
REASONS_AR = {
    "VATEX-SA-32": "تصدير سلع",
    "VATEX-SA-33": "تصدير خدمات",
    "VATEX-SA-34-1": "نقل دولي للبضائع",
    "VATEX-SA-34-2": "نقل دولي للركاب",
    "VATEX-SA-34-3": "خدمات مرتبطة بالنقل الدولي للركاب",
    "VATEX-SA-34-4": "توريد وسيلة نقل مؤهلة",
    "VATEX-SA-34-5": "خدمات متعلقة بنقل البضائع أو الركاب",
    "VATEX-SA-35": "أدوية ومستلزمات طبية",
    "VATEX-SA-36": "معادن مؤهلة (ذهب/فضة/بلاتين استثماري)",
    "VATEX-SA-EDU": "تعليم خاص لمواطن",
    "VATEX-SA-HEA": "رعاية صحية خاصة لمواطن",
    "VATEX-SA-29": "خدمات مالية (المادة 29)",
    "VATEX-SA-29-7": "تأمين على الحياة (المادة 29)",
    "VATEX-SA-30": "تعاملات عقارية (المادة 30)",
    "VATEX-SA-OOS": "خارج نطاق الضريبة",
}

SCHEMES = {
    "CRN": ("السجل التجاري", "CR No."),
    "MOM": ("ترخيص وزارة البلديات", "MOMRAH licence"),
    "MLS": ("ترخيص وزارة الموارد البشرية", "MHRSD licence"),
    "700": ("الرقم الموحد", "Unified No."),
    "SAG": ("ترخيص وزارة الاستثمار", "MISA licence"),
    "NAT": ("الهوية الوطنية", "National ID"),
    "GCC": ("هوية خليجية", "GCC ID"),
    "IQA": ("رقم الإقامة", "Iqama No."),
    "PAS": ("جواز السفر", "Passport"),
    "TIN": ("الرقم المميز", "TIN"),
    "OTH": ("معرّف", "ID"),
}


def render(invoice: PrintedInvoice, xml: bytes, design: InvoiceDesign, *, cleared: bool) -> bytes:
    """Two passes: the first counts the pages, so every footer can say "page 1 of 2"."""
    pages = _Document(invoice, xml, design, cleared=cleared, total_pages=None).build().pages_count
    return bytes(_Document(invoice, xml, design, cleared=cleared, total_pages=pages).build().output())


class _Document(FPDF):
    def __init__(self, invoice: PrintedInvoice, xml: bytes, design: InvoiceDesign, *, cleared: bool, total_pages: Optional[int]) -> None:
        super().__init__(orientation="P", unit="mm", format="A4", enforce_compliance="PDF/A-3B")
        self.invoice, self.xml, self.design, self.cleared, self.total_pages = invoice, xml, design, cleared, total_pages
        self.accent = _rgb(design.accent)
        self.tint = tuple(round(255 - (255 - c) * 0.09) for c in self.accent)
        self.on_accent = INK if _luminance(self.accent) > 0.55 else WHITE
        # An accent too pale to read as text on white prints the name in ink.
        self.accent_text = INK if _luminance(self.accent) > 0.55 else self.accent
        self.rates = sorted({line.rate for line in invoice.lines})
        self.show_rate = len(self.rates) > 1
        self.extras = [invoice.items[i] if i < len(invoice.items) else {} for i in range(len(invoice.lines))]
        self.has_item_discount = any((extra.get("item_discount") or 0) > 0 for extra in self.extras)
        self.reasons = [self._reason_code(line, extra) for line, extra in zip(invoice.lines, self.extras)]
        self.mixed_treatments = len({(line.category, code) for line, code in zip(invoice.lines, self.reasons)}) > 1
        self.set_auto_page_break(False)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        fonts = resources.files("zatca_tools.pdf").joinpath("fonts")
        for style, name in (("", "IBMPlexSansArabic-Regular.ttf"), ("B", "IBMPlexSansArabic-Bold.ttf")):
            with resources.as_file(fonts.joinpath(name)) as path:
                self.add_font("Plex", style, str(path))
        self.set_text_shaping(True)

    # -- the document ------------------------------------------------------------------

    def build(self) -> "_Document":
        inv = self.invoice
        arabic, english = TITLES[(inv.kind, inv.simplified)]
        self.set_title(f"{english} {inv.number}")
        self.set_author(inv.seller.name)
        self.set_subject(f"{english} {inv.number} — {inv.seller.name}")
        self.set_creator("ZATCA Tools SDK")
        self.set_lang("ar")
        self.set_creation_date(datetime.now(timezone.utc))
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        self.add_output_intent(
            OutputIntentSubType.PDFA,
            output_condition_identifier="sRGB",
            output_condition="sRGB IEC61966-2.1",
            registry_name="http://www.color.org",
            dest_output_profile=PDFICCProfile(contents=icc, n=3, alternate="DeviceRGB"),
            info="sRGB IEC61966-2.1",
        )
        self.embed_file(
            bytes=self.xml,
            basename=_filename(inv.number) + ".xml",
            mime_type="text/xml",
            associated_file_relationship="Alternative",
            desc="ZATCA's cleared e-invoice (UBL 2.1)" if self.cleared else "The signed e-invoice (UBL 2.1)",
            modification_date=datetime.now(timezone.utc),
        )

        self.add_page()
        y = self._header(arabic, english)
        y = self._details(y)
        y = self._parties(y)
        y = self._items(y)
        self._totals(y)
        return self

    def footer(self) -> None:
        y = PAGE_H - 13.0
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(MARGIN, y - 2.5, PAGE_W - MARGIN, y - 2.5)
        total = self.total_pages or self.page_no()
        arabic = f"صفحة {self.page_no()} من {total}"
        self._text(MARGIN, y, CONTENT_W, arabic, 7.5, color=MUTED, align="R")
        self._text(MARGIN, y, CONTENT_W - self._width(arabic, 7.5) - 3, f"Page {self.page_no()} of {total}", 7.5, color=MUTED, align="R")
        if self.design.zatca_tools_mark:
            self._mark(MARGIN, y)

    # -- sections ----------------------------------------------------------------------

    def _header(self, arabic: str, english: str) -> float:
        top = MARGIN
        logo = self.design.logo_bytes()
        if logo:
            self.image(_on_white(logo), x=MARGIN, y=top, w=48, h=20, keep_aspect_ratio=True)
        else:
            self._text(MARGIN, top + 2, 90, self.invoice.seller.name, 13, bold=True, color=self.accent_text, align="L")
        self._text(PAGE_W - MARGIN - 110, top + 1, 110, arabic, 19, bold=True, align="R")
        self._text(PAGE_W - MARGIN - 110, top + 11, 110, english, 10, color=MUTED, align="R")
        self.set_fill_color(*self.accent)
        self.rect(MARGIN, top + 24, CONTENT_W, 1.1, style="F")
        return top + 30

    def _details(self, y: float) -> float:
        inv = self.invoice
        fields = [("رقم الفاتورة", "Invoice No.", inv.number), ("تاريخ الإصدار", "Issue date", inv.issue_date)]
        if inv.simplified:
            fields.append(("وقت الإصدار", "Issue time", inv.issue_time))
        if inv.delivery_date:
            fields.append(("تاريخ التوريد", "Supply date", inv.delivery_date))
        if inv.original_invoice:
            fields.append(("الفاتورة الأصلية", "Original invoice", inv.original_invoice))
        y = self._field_row(y, fields)
        if inv.reason:
            y = self._field_row(y + 1, [("سبب الإشعار", "Reason", inv.reason)])
        return y + 4

    def _field_row(self, y: float, fields: list[tuple[str, str, str]]) -> float:
        width = CONTENT_W / max(len(fields), 3)
        heights = []
        for i, (arabic, english, value) in enumerate(fields):
            x = PAGE_W - MARGIN - (i + 1) * width  # first field on the right
            self._label(x + 1.5, y + 1.5, width - 3, arabic, english)
            heights.append(self._wrapped(x + 1.5, y + 6.5, width - 3, value, 10, bold=True, align="R"))
        bottom = y + 7.5 + max(heights)
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(MARGIN, bottom, PAGE_W - MARGIN, bottom)
        return bottom

    def _parties(self, y: float) -> float:
        inv = self.invoice
        boxes = [("البائع", "Seller", inv.seller)]
        if inv.buyer is not None and inv.buyer.name:
            boxes.append(("المشتري", "Buyer", inv.buyer))
        gap = 5.0
        width = (CONTENT_W - gap) / 2 if len(boxes) == 2 else CONTENT_W
        bottoms = []
        for i, (arabic, english, party) in enumerate(boxes):
            x = PAGE_W - MARGIN - width - i * (width + gap)
            bottoms.append(self._party(x, y, width, arabic, english, party))
        return max(bottoms) + 6

    def _party(self, x: float, y: float, width: float, arabic: str, english: str, party: Party) -> float:
        self.set_fill_color(*self.tint)
        self.rect(x, y, width, 7, style="F")
        self._label(x + 2, y + 1.6, width - 4, arabic, english, color=INK)
        cursor = y + 9
        cursor += self._wrapped(x + 2, cursor, width - 4, party.name, 10.5, bold=True, align="R") + 1
        rows = []
        if party.vat_number:
            rows.append(("الرقم الضريبي", "VAT No.", party.vat_number))
        if party.identifier:
            ar, en = SCHEMES.get(party.identifier_scheme or "OTH", SCHEMES["OTH"])
            rows.append((ar, en, party.identifier))
        for ar, en, value in rows:
            # Label and value in cells of their own: a number sharing a cell
            # with Arabic is lost to some PDF text extractors.
            self._text(x + 2, cursor, width - 4, ar, 8, color=MUTED, align="R")
            self._text(x + 2, cursor, width - 4 - self._width(ar, 8) - 2.5, value, 8.5, align="R")
            self._text(x + 2, cursor, width - 4, en, 7, color=MUTED, align="L")
            cursor += 4.6
        for line in party.address_lines:
            cursor += self._wrapped(x + 2, cursor, width - 4, line, 8.5, color=MUTED, align="R")
        self.set_draw_color(*RULE)
        self.rect(x, y, width, cursor - y + 2, style="D")
        return cursor + 2

    # -- items -------------------------------------------------------------------------

    def _columns(self) -> list[tuple[str, str, str, float]]:
        """(key, arabic, english, width) from right to left; the item column takes what is left."""
        rate = "" if self.show_rate or not self.rates else f" ({_quantity(self.rates[0])}%)"
        fixed = [("no", "#", "", 7.0), ("item", "الوصف", "Description", 0.0), ("qty", "الكمية", "Qty", 14.0), ("price", "سعر الوحدة", "Unit price", 21.0)]
        if self.has_item_discount:
            fixed.append(("discount", "الخصم", "Discount", 17.0))
        fixed.append(("net", "قبل الضريبة", "Before VAT", 22.0))
        if self.show_rate:
            fixed.append(("rate", "النسبة", "Rate", 13.0))
        fixed += [("vat", f"الضريبة{rate}", f"VAT{rate}", 20.0), ("gross", "شامل الضريبة", "Incl. VAT", 23.0)]
        flexible = CONTENT_W - sum(width for *_, width in fixed)
        return [(key, ar, en, width or flexible) for key, ar, en, width in fixed]

    def _table_header(self, y: float) -> float:
        x = PAGE_W - MARGIN
        self.set_fill_color(*self.accent)
        self.rect(MARGIN, y, CONTENT_W, 9, style="F")
        for key, arabic, english, width in self._columns():
            x -= width
            align = "R" if key == "item" else "C"
            self._text(x + 1.5, y + 0.8, width - 3, arabic, 8, bold=True, color=self.on_accent, align=align)
            if english:
                self._text(x + 1.5, y + 4.6, width - 3, english, 6.5, color=self.on_accent, align=align)
        return y + 9

    def _items(self, y: float) -> float:
        y = self._table_header(y)
        for index, line in enumerate(self.invoice.lines):
            notes = self._notes(index)
            height = self._row_height(line.name, notes)
            if y + height > FOOTER_TOP - 2:
                y = self._table_header(self._continued())
            self._row(y, index, line, notes, height)
            y += height
        return y + 5

    def _notes(self, index: int) -> list[str]:
        """What prints under an item's name: its description, and on a mixed invoice its exemption reason."""
        notes = []
        if self.extras[index].get("description"):
            notes.append(str(self.extras[index]["description"]))
        code = self.reasons[index]
        if self.mixed_treatments and code:
            notes.append(f"سبب الإعفاء: {REASONS_AR.get(code, code)}")
            notes.append(f"VAT exemption reason: {self._reason_text(code)}")
        return notes

    def _row_height(self, name: str, notes: list[str]) -> float:
        width = dict((key, w) for key, _, _, w in self._columns())["item"] - 3
        height = self._measure(width, name, 9, bold=False) + 3.4
        for note in notes:
            height += self._measure(width, note, 7.5, bold=False)
        return max(height, 10.5 if self.has_item_discount else 8.0)

    def _row(self, y: float, index: int, line, notes: list[str], height: float) -> None:
        extra = self.extras[index]
        discounted = (extra.get("item_discount") or 0) > 0
        values = {
            "no": line.number,
            "qty": _quantity(line.quantity),
            "price": _money(float(extra["agreed_price"]) if discounted and extra.get("agreed_price") is not None else line.unit_price),
            "discount": "-" + _money(float(extra["item_discount"])) if discounted else "",
            "net": _money(line.net),
            "rate": f"{_quantity(line.rate)}%",
            "vat": _money(line.tax),
            "gross": _money(line.total),
        }
        x = PAGE_W - MARGIN
        for key, _, _, width in self._columns():
            x -= width
            if key == "item":
                used = self._wrapped(x + 1.5, y + 1.7, width - 3, line.name, 9, align="R")
                for note in notes:
                    used += self._wrapped(x + 1.5, y + 1.7 + used, width - 3, note, 7.5, color=MUTED, align="R")
            else:
                self._text(x + 1, y + 1.7, width - 2, values[key], 9, bold=key == "gross", align="C")
                if key == "discount" and discounted:
                    self._text(x + 1, y + 6.0, width - 2, "-" + self._discount_percent(line, extra) + "%", 7, color=MUTED, align="C")
        self.set_draw_color(*RULE)
        self.set_line_width(0.2)
        self.line(MARGIN, y + height, PAGE_W - MARGIN, y + height)

    @staticmethod
    def _discount_percent(line, extra: dict) -> str:
        if extra.get("discount_type") == "percent" and extra.get("discount_value") is not None:
            return _quantity(float(extra["discount_value"]))
        before = line.quantity * float(extra.get("agreed_price") or 0)
        return _quantity(round(float(extra["item_discount"]) / before * 100, 1)) if before > 0 else "0"

    def _reason_code(self, line, extra: dict) -> Optional[str]:
        if line.category == "S":
            return None
        if extra.get("tax_reason_code"):
            return str(extra["tax_reason_code"])
        codes = {s.reason_code for s in self.invoice.subtotals if s.category == line.category and s.reason_code}
        return codes.pop() if len(codes) == 1 else None

    def _reason_text(self, code: str) -> str:
        return next((s.reason for s in self.invoice.subtotals if s.reason_code == code and s.reason), code)

    # -- totals ------------------------------------------------------------------------

    def _totals(self, y: float) -> None:
        rows = self._total_rows()
        note = self._exemption_note()
        needed = 8.4 * len(rows) + 22 + (self._measure(100, note, 7, bold=False) if note else 0)
        if y + max(needed, 46) > FOOTER_TOP - self._footer_text_height() - 2:
            y = self._continued()

        qr_size = 36.0
        if self.invoice.qr:
            self.image(_qr_png(self.invoice.qr), x=PAGE_W - MARGIN - qr_size, y=y, w=qr_size, h=qr_size)

        box_w, x = 108.0, MARGIN
        cursor = y
        for arabic, english, amount in rows:
            self._text(x + 2, cursor + 1.2, 36, amount, 9, align="L")
            self._text(x + 38, cursor + 1.2, box_w - 40, arabic, 9, align="R")
            self._text(x + 38, cursor + 5.0, box_w - 40, english, 6.5, color=MUTED, align="R")
            cursor += 8.4
        # The total due: the one figure the reader came for.
        self.set_fill_color(*self.tint)
        self.rect(x, cursor, box_w, 10, style="F")
        self._text(x + 2, cursor + 2.2, 50, f"{_money(self.invoice.payable)} {self.invoice.currency}", 11, bold=True, align="L")
        self._text(x + 52, cursor + 1.4, box_w - 54, "الإجمالي المستحق", 10.5, bold=True, align="R")
        self._text(x + 52, cursor + 6.2, box_w - 54, "TOTAL DUE", 6.5, color=MUTED, align="R")
        cursor += 11
        self._text(x + 2, cursor, box_w - 4, "شامل ضريبة القيمة المضافة  ·  Amount includes VAT", 7, color=MUTED, align="R")
        cursor += 4.5
        self._text(x + 2, cursor, box_w - 4, f"جميع المبالغ بـ{self._currency()}  ·  All amounts in {self._currency_en()}", 7, color=MUTED, align="R")
        cursor += 4.5
        if note:
            cursor += self._wrapped(x + 2, cursor, box_w - 4, note, 7, color=MUTED, align="R")
        cursor = max(cursor + 4, y + qr_size + 2)

        if self.design.footer:
            self._wrapped(MARGIN, max(cursor + 4, FOOTER_TOP - self._footer_text_height()), CONTENT_W, self.design.footer, 8, color=MUTED, align="C")

    def _total_rows(self) -> list[tuple[str, str, str]]:
        """The platform's totals: before the discount and the discount (when there is one), the total, the VAT."""
        inv = self.invoice
        rows: list[tuple[str, str, str]] = []
        gross = inv.gross if inv.gross is not None else (inv.line_total if inv.allowance > 0 else None)
        if gross is not None and (inv.discount or inv.allowance) > 0:
            rows.append(("الإجمالي قبل الخصم", "Gross", _money(gross)))
            rows.append(("الخصم", "Discount", "-" + _money(max(0.0, round(gross - inv.taxable, 2)))))
        rows.append(("الإجمالي", "Total", _money(inv.taxable)))
        rate = f" ({_quantity(self.rates[0])}%)" if len(self.rates) == 1 else ""
        rows.append((f"ضريبة القيمة المضافة{rate}", "Total VAT", _money(inv.tax)))
        return rows

    def _exemption_note(self) -> Optional[str]:
        """On an invoice of one treatment, its exemption reason once, under the totals."""
        if self.mixed_treatments:
            return None
        codes = list(dict.fromkeys(code for code in self.reasons if code))
        return "\n".join(f"سبب الإعفاء من الضريبة: {REASONS_AR.get(code, code)}  ·  VAT exemption reason: {self._reason_text(code)}" for code in codes) or None

    def _continued(self) -> float:
        """A new page that says which invoice it continues."""
        self.add_page()
        arabic, english = TITLES[(self.invoice.kind, self.invoice.simplified)]
        self._text(MARGIN, MARGIN, CONTENT_W, f"{arabic} {self.invoice.number} — تابع", 8.5, bold=True, color=MUTED, align="R")
        self._text(MARGIN, MARGIN, CONTENT_W, f"{english} {self.invoice.number} — continued", 8, color=MUTED, align="L")
        return MARGIN + 7

    def _footer_text_height(self) -> float:
        return self._measure(CONTENT_W, self.design.footer, 8, bold=False) + 2 if self.design.footer else 0.0

    def _currency(self) -> str:
        return "الريال السعودي" if self.invoice.currency == "SAR" else self.invoice.currency

    def _currency_en(self) -> str:
        return "Saudi Riyal (SAR)" if self.invoice.currency == "SAR" else self.invoice.currency

    def _mark(self, x: float, y: float) -> None:
        """The ZATCA Tools mark — the logo the site wears — with the name and the address."""
        mark = resources.files("zatca_tools.pdf").joinpath("zatca-tools-mark.png")
        self.image(io.BytesIO(mark.read_bytes()), x=x, y=y - 1.2, w=5.2, h=5.2)
        self._text(x + 6.2, y, 40, "ZATCA Tools", 7.5, bold=True, align="L")
        self._text(x + 6.2 + self._width("ZATCA Tools", 7.5, bold=True) + 2.2, y, 60, "zatcatools.com", 7.5, color=MUTED, align="L")

    # -- text primitives ---------------------------------------------------------------

    def _label(self, x: float, y: float, width: float, arabic: str, english: str, color=MUTED) -> None:
        self._text(x, y, width, arabic, 7.5, bold=True, color=color, align="R")
        self._text(x, y, width, english, 6.8, color=MUTED, align="L")

    def _text(self, x: float, y: float, width: float, text: str, size: float, *, bold: bool = False, color=INK, align: str = "R") -> None:
        self.set_font("Plex", "B" if bold else "", size)
        self.set_text_color(*color)
        self.set_xy(x, y)
        self.cell(width, size * 0.42, _isolate(str(text)), align=align)

    def _wrapped(self, x: float, y: float, width: float, text: str, size: float, *, bold: bool = False, color=INK, align: str = "R") -> float:
        """Several lines if need be; returns the height used."""
        self.set_font("Plex", "B" if bold else "", size)
        self.set_text_color(*color)
        self.set_xy(x, y)
        line = size * 0.45
        text = _isolate(str(text))
        lines = self.multi_cell(width, line, text, align=align, dry_run=True, output="LINES")
        self.set_xy(x, y)
        self.multi_cell(width, line, text, align=align)
        return line * max(len(lines), 1)

    def _width(self, text: str, size: float, *, bold: bool = False) -> float:
        self.set_font("Plex", "B" if bold else "", size)
        return self.get_string_width(_isolate(text))

    def _measure(self, width: float, text: Optional[str], size: float, *, bold: bool) -> float:
        if not text:
            return 0.0
        self.set_font("Plex", "B" if bold else "", size)
        return size * 0.45 * max(len(self.multi_cell(width, size * 0.45, _isolate(text), dry_run=True, output="LINES")), 1)


def _isolate(text: str) -> str:
    if not _ARABIC.search(text):
        return text
    return _PERCENT.sub(lambda m: "\u2066" + m.group(0) + "\u2069", text)


def _qr_png(payload: str) -> io.BytesIO:
    """The QR code as an image — a vector form XObject without resources fails PDF/A (rule 6.2.2-2)."""
    buffer = io.BytesIO()
    segno.make(payload, error="l", micro=False).save(buffer, kind="png", scale=8, border=1)
    buffer.seek(0)
    return buffer


def _on_white(data: bytes) -> io.BytesIO:
    """The logo flattened onto white and capped at 600 px: a transparent logo never prints black."""
    image = Image.open(io.BytesIO(data))
    image.thumbnail((600, 600))
    canvas = Image.new("RGB", image.size, WHITE)
    rgba = image.convert("RGBA")
    canvas.paste(rgba, mask=rgba.split()[3])
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _luminance(rgb: tuple[int, int, int]) -> float:
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _quantity(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _filename(number: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in number).strip("-.")
    return safe or "invoice"
