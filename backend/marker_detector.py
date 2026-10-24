import re

MARKERS = {
    "Құрылым": ["кіріспе ретінде", "қазіргі таңда", "біріншіден", "екіншіден"],
    "Тезис және пікір": ["менің ойымша", "менің пікірімше", "мен бұл пікірмен келісемін", "мен бұл пікірмен келіспеймін"],
    "Аргумент": ["себебі", "өйткені", "оның басты себебі"],
    "Қарсы пікір және салыстыру": ["алайда", "дегенмен", "керісінше", "бір жағынан", "екінші жағынан"],
    "Дәлел": ["мысалы", "дәлел ретінде", "бұған дәлел ретінде", "зерттеулерге сәйкес", "деректерге сүйенсек"],
    "Қорытынды": ["қорытындылай келе", "демек", "осылайша", "жалпы алғанда"],
}


def detect_markers(text: str, categories: dict | None = None) -> list[dict]:
    result = []
    for sentence in re.finditer(r"[^.!?\n]+[.!?]?", text):
        for category, phrases in (categories or MARKERS).items():
            pattern = r"(?<!\w)(?:" + "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True)) + r")(?!\w)"
            for match in re.finditer(pattern, sentence.group(), re.IGNORECASE):
                result.append({"category": category, "text": match.group(),
                               "start": sentence.start() + match.start(),
                               "end": sentence.start() + match.end(),
                               "sentence": sentence.group().strip()})
    return sorted(result, key=lambda item: item["start"])
