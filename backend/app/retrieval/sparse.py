"""BM25-style sparse vectors without any model download.

We send BM25's term-frequency half (with length normalisation); Qdrant applies the IDF half on the
server because the sparse vector is configured with Modifier.IDF. Tokens are hashed to integer
ids, so any language works (Hindi included) and no vocabulary has to be stored.
"""
import re
import zlib
from collections import Counter

from qdrant_client import models

K1 = 1.2
B = 0.75
AVG_DOC_TOKENS = 300.0  # typical chunk length; only used for length normalisation

# Plain \w+ splits Indic words at vowel signs/viramas (रिफंड -> र, फ, ड) because combining marks
# are not "word" characters. Explicitly include the Indic blocks (Devanagari ... Malayalam/Sinhala)
# and Arabic diacritics so Hindi, Marathi, Bengali, Tamil, Urdu etc. tokenize as whole words.
_TOKEN = re.compile(r"[\wऀ-෿ً-ٟ]+", re.UNICODE)
_STOP = frozenset(
    "a an the is are was were be been of to in on at for and or not it its this that these those "
    "as by with from what how do does did i my me can will "
    # Hinglish (romanised Hindi) filler
    "hai hain ho hota hoti hote tha thi the ka ki ke ko me mein se par pe kya kab kitna kitne kitni "
    "kaise kaun kahan kyon aur ya bhi nahi nahin to toh ye yeh vo wo woh mera meri mere aap kuch "
    "ek is us in un liye "
    # Devanagari filler
    "है हैं हो होता होती होते था थी थे का की के को में से पर क्या कब कितना कितने कितनी कैसे कौन कहाँ "
    "क्यों और या भी नहीं तो यह ये वह वो मेरा मेरी मेरे आप कुछ एक इस उस इन उन लिए".split()
)


def _singular(t: str) -> str:
    """Tiny stemmer: 'refunds' -> 'refund', 'days' -> 'day'. Plain English plurals only, so a query
    word still matches its plural in the document (no stemming library, same rule at index time)."""
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss") and t.isascii() and t.isalpha():
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1 or t.isdigit()]
    return [_singular(t) for t in tokens]


def _token_id(token: str) -> int:
    return zlib.crc32(token.encode("utf-8")) & 0x7FFFFFFF  # stable across processes and restarts


def document_vector(text: str) -> models.SparseVector:
    """BM25 term weights for a chunk: tf*(k1+1) / (tf + k1*(1-b+b*len/avg))."""
    tokens = tokenize(text)
    if not tokens:
        return models.SparseVector(indices=[], values=[])
    norm = K1 * (1 - B + B * len(tokens) / AVG_DOC_TOKENS)
    weights: dict[int, float] = {}
    for token, tf in Counter(tokens).items():
        weights[_token_id(token)] = tf * (K1 + 1) / (tf + norm)
    return models.SparseVector(indices=list(weights), values=[round(v, 4) for v in weights.values()])


def query_vector(text: str) -> models.SparseVector:
    """Each distinct query term counts once; the server multiplies by IDF."""
    ids = sorted({_token_id(t) for t in tokenize(text)})
    return models.SparseVector(indices=ids, values=[1.0] * len(ids))