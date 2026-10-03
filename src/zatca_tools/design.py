"""How a printed invoice looks: yours to brand, ours to lay out.

The layout — what is printed and where — is fixed, because the contents of a
tax invoice are set by the regulations and a design must never be able to drop
or move one. What is yours: the logo, the accent colour, a footer of your own,
and whether the ZATCA Tools mark appears.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from .errors import ValidationError

#: ZATCA Tools green.
DEFAULT_ACCENT = "#0E8345"

_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")


@dataclass(frozen=True)
class InvoiceDesign:
    """The branding of a printed invoice.

    * ``logo``   — a PNG, JPEG or WebP image, as a path or bytes. Printed top-left,
                   on white (a transparent logo is flattened, never turned black).
    * ``accent`` — your colour, ``#RRGGBB``: the table header and the total.
    * ``footer`` — your own text at the foot of the invoice (terms, bank details).
                   Several lines are fine.
    * ``zatca_tools_mark`` — the small "ZATCA Tools" mark in the page footer of the
                   default design. Set False to print without it.
    """

    logo: Optional[Union[str, Path, bytes]] = None
    accent: str = DEFAULT_ACCENT
    footer: Optional[str] = None
    zatca_tools_mark: bool = True

    def __post_init__(self) -> None:
        problems = []
        match = _HEX.match(str(self.accent or ""))
        if not match:
            problems.append({"field": "design.accent", "message": "design.accent is a colour like #0E8345."})
        else:
            object.__setattr__(self, "accent", "#" + match.group(1).upper())
        if self.logo is not None and not isinstance(self.logo, (str, Path, bytes, bytearray)):
            problems.append({"field": "design.logo", "message": "design.logo is a file path or the image's bytes."})
        elif isinstance(self.logo, (str, Path)) and not Path(self.logo).is_file():
            problems.append({"field": "design.logo", "message": f"design.logo: no file at {self.logo}."})
        if self.footer is not None and not isinstance(self.footer, str):
            problems.append({"field": "design.footer", "message": "design.footer is text."})
        elif self.footer is not None and len(self.footer) > 2000:
            problems.append({"field": "design.footer", "message": "design.footer is at most 2,000 characters."})
        if problems:
            raise ValidationError("The invoice design has a problem.", problems)

    @classmethod
    def parse(cls, value: Union["InvoiceDesign", Mapping[str, Any], None]) -> "InvoiceDesign":
        if value is None:
            return cls()
        if isinstance(value, InvoiceDesign):
            return value
        if isinstance(value, Mapping):
            unknown = set(value) - {"logo", "accent", "footer", "zatca_tools_mark"}
            if unknown:
                raise ValidationError("The invoice design has a problem.", [{"field": f"design.{key}", "message": f"'{key}' is not a design option: logo, accent, footer or zatca_tools_mark."} for key in sorted(unknown)])
            return cls(**dict(value))
        raise ValidationError("The invoice design has a problem.", [{"field": "design", "message": "design is an InvoiceDesign or a dict of its options."}])

    def logo_bytes(self) -> Optional[bytes]:
        if self.logo is None:
            return None
        return bytes(self.logo) if isinstance(self.logo, (bytes, bytearray)) else Path(self.logo).read_bytes()


__all__ = ["DEFAULT_ACCENT", "InvoiceDesign"]
