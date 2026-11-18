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
    "as by with from what how do does did i my me can will".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 1 or t.isdigit()]


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
