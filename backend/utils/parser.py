"""
Shared parsing helpers used across scrapers.
"""
import re
from typing import Optional
 
 
def normalize(text: str) -> str:
    """
     Lowercase and collapse any run of non-alphanumerics to a single space.
    """
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
 
 
def match_brand(text: str, brands: list[str]) -> Optional[str]:
    """
     Find which brand from `brands` appears in `text` (a product name or
     description), using the response's brand vocabulary as a closed set.
 
     Matching is on normalized, space-padded tokens, so punctuation and case
     don't matter and we don't match inside a larger word ("Daisy" won't match
     "Daisychain"). When more than one brand matches, the longest / most specific
     one wins. Returns the brand exactly as it was provided in `brands`.
    """
    if not text or not brands:
        return None
    padded = f" {normalize(text)} "
    candidates = sorted(
        ((normalize(b), b) for b in brands),
        key=lambda t: len(t[0]),
        reverse=True,
    )
    for norm, original in candidates:
        if norm and f" {norm} " in padded:
            return original
    return None

# ── size parsing / normalization ────────────────────────────────────────────
 
_MEASURE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(fl\s*oz|oz|lbs?|kg|g|ml|l|liter|litre|gal|gallon|qt|pt)\b",
    re.IGNORECASE,
)
_PACK_RE = re.compile(
    r"(\d+)\s*[- ]?\s*(packs?|pk|count|ct|ctn|pieces?|pcs?)\b",
    re.IGNORECASE,
)
 
# token -> (base_unit, factor_to_base). volume -> ml, weight -> g, count -> ct.
_TO_BASE = {
    "fl oz": ("ml", 29.5735), "ml": ("ml", 1.0), "l": ("ml", 1000.0),
    "liter": ("ml", 1000.0), "litre": ("ml", 1000.0),
    "gal": ("ml", 3785.41), "gallon": ("ml", 3785.41),
    "qt": ("ml", 946.353), "pt": ("ml", 473.176),
    "oz": ("g", 28.3495), "lb": ("g", 453.592), "lbs": ("g", 453.592),
    "kg": ("g", 1000.0), "g": ("g", 1.0),
}
 
 
def _clean_unit(u: str) -> str:
    return re.sub(r"\s+", " ", u.lower()).strip()
 
 
def parse_size_from_name(name: str) -> Optional[str]:
    """
    Extract a human-readable size from a product name: the first measurement
    ("8 oz", "16.9 fl oz", "1 gallon") plus any pack count ("6 Pack" -> "6 pk"),
    joined for display. Returns None when the name carries no size. This is exact
    (no derivation error), so it's the preferred source for the `size` field.
    """
    if not name:
        return None
    parts = []
    m = _MEASURE_RE.search(name)
    if m:
        parts.append(f"{m.group(1)} {_clean_unit(m.group(2))}")
    p = _PACK_RE.search(name)
    if p:
        parts.append(f"{p.group(1)} pk")
    return ", ".join(parts) if parts else None
 
 
def parse_unit(price_string: str) -> Optional[tuple[float, str]]:
    """
    (multiplier, unit) from a unit-price display string like '17.1 ¢/oz' or
    '$1.99/100 ct'. Multiplier defaults to 1. Returns None if no unit is found.
    """
    if not price_string:
        return None
    m = re.search(
        r"/\s*(?:(\d+(?:\.\d+)?)\s*)?(fl\s*oz|oz|lbs?|kg|g|ml|l|gal|gallon|qt|pt|ct|ea|each)\b",
        price_string,
        re.IGNORECASE,
    )
    if not m:
        return None
    mult = float(m.group(1)) if m.group(1) else 1.0
    return mult, _clean_unit(m.group(2))
 
 
def derive_quantity(
    price: Optional[float],
    unit_value: Optional[float],
    price_string: str,
) -> Optional[tuple[float, str]]:
    """
    Fallback for when the name has no parseable size: total quantity =
    price / unit_value * multiplier, in the unit-price's unit, rounded to a
    tenth. Uses the NUMERIC unit price for the value (more precise than the
    displayed string). The result is approximate — a coarsely-rounded unit price
    can shift the tenth — so prefer parse_size_from_name when a size is present.
    """
    if not price or not unit_value:
        return None
    pu = parse_unit(price_string or "")
    if not pu:
        return None
    mult, unit = pu
    return round(price / unit_value * mult, 1), unit
 
 
def normalize_size(size_str: str) -> Optional[tuple[float, str]]:
    """
    Canonical total quantity for cross-store merge matching, as (value, base_unit)
    with base_unit in {ml, g, ct}: measurement x pack, converted to a base unit.
    Intended to be called at merge time, not stored on the product. Compare only
    within the same base_unit and with a small tolerance, since derived sizes
    wobble and pack phrasing varies across stores. Note: bare 'oz' is treated as
    weight and 'fl oz' as volume — the one ambiguity to watch when matching.
    """
    if not size_str:
        return None
    p = _PACK_RE.search(size_str)
    packn = float(p.group(1)) if p else 1.0
    m = _MEASURE_RE.search(size_str)
    if m:
        conv = _TO_BASE.get(_clean_unit(m.group(2)))
        if conv:
            base_unit, factor = conv
            return round(float(m.group(1)) * packn * factor, 2), base_unit
    if p:
        return packn, "ct"  # count-only, e.g. "12 ct"
    return None