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