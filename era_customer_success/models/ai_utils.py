import json
import re


def extract_json_object(raw):
    if not raw:
        return None
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z]*', '', text).strip().rstrip('`').strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
