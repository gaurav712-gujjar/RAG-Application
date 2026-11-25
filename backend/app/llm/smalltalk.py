"""Greetings and thanks get a canned reply: no search (not even the web), no LLM call, no tokens."""
import re

from app.llm.prompts import answer_language

_GREET = re.compile(
    r"^\s*(hi+|hello+|hey+|hii+|yo|hola|namaste|namaskar|नमस्ते|नमस्कार|हेलो|हाय|"
    r"good\s+(morning|afternoon|evening|night))\W*$",
    re.IGNORECASE,
)
_THANKS = re.compile(
    r"^\s*(thanks?|thank\s+you|thx|ty|shukriya|dhanyavad|धन्यवाद|शुक्रिया|ok(ay)?|cool|great|nice|"
    r"got\s+it|accha|acha|theek\s+hai|ठीक\s+है|अच्छा)\W*$",
    re.IGNORECASE,
)
_BYE = re.compile(r"^\s*(bye+|goodbye|see\s+you|alvida|tata|अलविदा)\W*$", re.IGNORECASE)

_REPLIES = {
    "greet": {
        "English": "Hi! Ask me anything about your documents, or switch to Web in Tools to search the internet.",
        "Hindi": "नमस्ते! अपने दस्तावेज़ों के बारे में कुछ भी पूछें, या Tools में Web चुनकर इंटरनेट पर खोजें।",
        "Hinglish": "Namaste! Apne documents ke baare me kuch bhi poochho, ya Tools me Web chunkar internet par search karo.",
    },
    "thanks": {
        "English": "You're welcome! Ask another question any time.",
        "Hindi": "आपका स्वागत है! कभी भी और सवाल पूछें।",
        "Hinglish": "Aapka swagat hai! Kabhi bhi aur sawal poochho.",
    },
    "bye": {
        "English": "Bye! Your chats are saved.",
        "Hindi": "अलविदा! आपकी चैट सेव हैं।",
        "Hinglish": "Bye! Aapki chats save hain.",
    },
}


def smalltalk(question: str) -> str | None:
    q = question.strip()
    if len(q) > 40:
        return None
    for kind, rx in (("greet", _GREET), ("thanks", _THANKS), ("bye", _BYE)):
        if rx.match(q):
            lang = answer_language(q)
            romanized = re.match(r"^\s*(namaste|namaskar|shukriya|dhanyavad|accha|acha|alvida|theek\s+hai|tata)\b", q, re.I)
            key = "Hindi" if lang == "Hindi" else "Hinglish" if (lang.startswith("Hinglish") or romanized) else "English"
            return _REPLIES[kind][key]
    return None
