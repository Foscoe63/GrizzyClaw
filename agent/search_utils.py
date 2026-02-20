"""Search query utilities for web search (DuckDuckGo, etc.)."""

import re


def correct_search_query(query: str) -> str:
    """Fix common typos in search queries (e.g. pecs->specs for tech context)."""
    if not query or len(query) < 3:
        return query
    q = query.lower()
    tech_indicators = (
        "mac", "iphone", "ipad", "studio", "ultra", "pro", "max", "mini",
        "specifications", "specs", "product", "release", "upcoming", "cpu",
        "gpu", "ram", "storage", "chip", "processor", "apple", "computer",
    )
    if "pecs" in q and any(ind in q for ind in tech_indicators):
        query = re.sub(r"\bpecs\b", "specs", query, flags=re.IGNORECASE)
    return query


def simplify_search_query(query: str) -> str:
    """Simplify queries to improve DuckDuckGo results (avoids bot detection, over-specificity)."""
    if not query or len(query) <= 10:
        return query
    filler = (
        r"\b(the|latest|upcoming|on|for|what|are|going|to|be|and|see|if|you|can|get|"
        r"information|about|look|search|internet|web)\b"
    )
    simplified = re.sub(filler, " ", query, flags=re.IGNORECASE)
    simplified = re.sub(r"\s+", " ", simplified).strip()
    if simplified and len(simplified) < len(query) and len(simplified) >= 10:
        return simplified
    return query


def simplify_search_query_retry(query: str) -> str:
    """More aggressive simplification for retry when first search returns no results."""
    simplified = simplify_search_query(query)
    if len(simplified) <= 30:
        return simplified
    words = simplified.split()
    product_words = []
    for w in words:
        if any(c.isdigit() for c in w) or w in ("Mac", "Studio", "Ultra", "Pro", "Max", "iPhone", "iPad"):
            product_words.append(w)
        elif product_words and w.lower() not in ("the", "and", "for"):
            product_words.append(w)
    if product_words:
        retry = " ".join(product_words)
        if len(retry) >= 10:
            return retry
    significant = [w for w in words if len(w) > 2 and w.lower() not in ("the", "for", "and", "are")]
    return " ".join(significant[:5]) if significant else simplified
