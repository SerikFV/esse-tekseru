"""Keep Gemini's output grammar small; enforce all limits with Pydantic afterward.

Pydantic's full schema contains repeated scoring definitions and large nested
string/array limits. They can exceed a provider's structured-output grammar
complexity budget. Bounds are still mandatory in AIAnalysis.model_validate.
"""
from copy import deepcopy


def grading_schema(schema: dict) -> dict:
    omitted = {"title", "default", "minLength", "maxLength", "minItems", "maxItems"}

    def simplify(value):
        if isinstance(value, list):
            return [simplify(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key in omitted:
                continue
            if key in {"properties", "$defs"}:
                # These keys are field/definition names, not schema keywords.
                result[key] = {name: simplify(definition) for name, definition in item.items()}
            elif key in {"enum", "const"}:
                result[key] = item
            else:
                result[key] = simplify(item)
        return result

    return simplify(deepcopy(schema))
