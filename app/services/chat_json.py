def extract_json_array(value: str) -> str:
    start = value.find("[")
    end = value.rfind("]")
    if start != -1 and end != -1 and end > start:
        return value[start : end + 1]
    return "[]"


def extract_json_object(value: str) -> str:
    start = value.find("{")
    end = value.rfind("}")
    if start != -1 and end != -1 and end > start:
        return value[start : end + 1]
    return "{}"
