"""Grounded text annotations and deterministic version comparison. No extra AI calls."""
import hashlib
import re
from difflib import SequenceMatcher

from .schemas import MAX_SCORES


def topic_segments(essay: str) -> list[str]:
    segments = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", essay):
        for start in range(0, len(sentence), 1800):
            chunk = sentence[start:start + 1800].strip()
            if chunk:
                segments.append(chunk)
    return segments


def annotations_for(essay: str, analysis: dict | None) -> list[dict]:
    if not analysis:
        return []
    findings = [dict(category="language", quote=item["original"], explanation=item["explanation"],
                     suggestion=item["corrected"]) for item in analysis.get("language_corrections", [])]
    findings.extend(analysis.get("text_feedback", []))
    result, seen = [], set()
    for finding in findings:
        quote = finding["quote"]
        # Anchor whitespace-normalized model quotes without changing the original source.
        pattern = r"\s+".join(re.escape(part) for part in quote.split())
        matches = list(re.finditer(pattern, essay)) if pattern else []
        if not matches:
            continue
        match = matches[0]
        key = (match.start(), match.end(), finding["category"])
        if key in seen:
            continue
        seen.add(key)
        result.append({**finding, "quote": match.group(), "start": match.start(), "end": match.end(),
                       "occurrences": len(matches), "id": hashlib.sha256(repr(key).encode()).hexdigest()[:16]})
    return sorted(result, key=lambda item: (item["start"], item["end"]))


def version_comparison(before: dict, after: dict) -> dict:
    old_tokens = re.findall(r"\s+|[^\s]+", before["essay_text"])
    new_tokens = re.findall(r"\s+|[^\s]+", after["essay_text"])
    old_parts, new_parts = [], []
    for tag, a, b, c, d in SequenceMatcher(None, old_tokens, new_tokens, autojunk=True).get_opcodes():
        if a < b:
            old_parts.append({"kind": "same" if tag == "equal" else "removed", "text": "".join(old_tokens[a:b])})
        if c < d:
            new_parts.append({"kind": "same" if tag == "equal" else "added", "text": "".join(new_tokens[c:d])})
    old_analysis, new_analysis = before.get("analysis"), after.get("analysis")
    score_changes = []
    if old_analysis and new_analysis:
        for key, maximum in MAX_SCORES.items():
            old, new = old_analysis["criteria"][key], new_analysis["criteria"][key]
            score_changes.append({"criterion": key, "before": old["score"], "after": new["score"],
                                  "delta": new["score"] - old["score"], "maximum": maximum,
                                  "before_feedback": old["weaknesses"], "after_feedback": new["weaknesses"]})
    observations = []
    for item in before.get("annotations", []):
        normalized = " ".join(item["quote"].split())
        still_present = normalized in " ".join(after["essay_text"].split())
        observations.append({"quote": item["quote"], "explanation": item["explanation"],
                             "status": "unchanged_quote" if still_present else "revised_quote"})
    return {"before_id": before["id"], "after_id": after["id"], "before_title": before["title"],
            "after_title": after["title"], "before_text": before["essay_text"], "after_text": after["essay_text"],
            "before_parts": old_parts, "after_parts": new_parts, "score_changes": score_changes,
            "total_delta": after["total_score"] - before["total_score"] if old_analysis and new_analysis else None,
            "observations": observations, "remaining_feedback": after.get("annotations", []),
            "argument_changes": {"before": (old_analysis or {}).get("structure_map", {}).get("arguments", []),
                                 "after": (new_analysis or {}).get("structure_map", {}).get("arguments", [])},
            "evidence_changes": {"before": (old_analysis or {}).get("structure_map", {}).get("evidence", []),
                                "after": (new_analysis or {}).get("structure_map", {}).get("evidence", [])},
            "text_changed": before["essay_text"] != after["essay_text"],
            "notice": "Балл айырмасы — AI бағасының өзгерісі. Өзгертілген үзінді қатенің міндетті түрде түзелгенін білдірмейді; жаңа түсініктемелерді салыстырыңыз."}
