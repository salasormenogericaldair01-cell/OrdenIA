"""Deterministic previews, snippets and basic Spanish/English terms."""

import re
import unicodedata
from collections import Counter

_WORDS = re.compile(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+", re.UNICODE)
_STOPWORDS = frozenset("""a al algo algunas algunos ante antes como con contra cual cuando de del desde donde durante e el ella ellas ellos en entre era es esa ese eso esta estas este estos ha han hasta hay la las le les lo los mas más me mi mis mucho muy no nos o para pero por porque que quien quienes se ser si sin sobre son su sus te tener the of and or to in for on with from is are was were be by as at this that these those it its an a you your we our not can will into than then all any each also use using""".split())


def _fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", value.casefold()) if not unicodedata.combining(char))


def keywords(text: str, limit: int = 15) -> tuple[str, ...]:
    frequencies: Counter[str] = Counter()
    display: dict[str, str] = {}
    for match in _WORDS.finditer(text):
        word = match.group()
        key = _fold(word)
        if (len(key) < 3 and not any(char.isdigit() for char in key)) or key in _STOPWORDS or key.isdigit():
            continue
        frequencies[key] += 1
        if key not in display or (word.isupper() and not display[key].isupper()):
            display[key] = word
    return tuple(display[key] for key, _ in frequencies.most_common(limit))


def preview(text: str, limit: int = 500) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    end = compact.rfind(" ", 0, limit)
    return compact[: end if end > limit // 2 else limit].rstrip() + "…"


def snippet(text: str, query: str, radius: int = 75) -> str:
    if not text:
        return ""
    folded = _fold(text)
    terms = [_fold(word) for word in _WORDS.findall(query)]
    position = folded.find(_fold(query.strip().strip('"')))
    if position < 0:
        position = next((folded.find(term) for term in terms if folded.find(term) >= 0), 0)
    start = max(0, position - radius)
    end = min(len(text), position + max((len(term) for term in terms), default=0) + radius)
    return ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")
