"""Palavras de luz e hora do dia nos textos que chegam aos prompts (para avisar quando a bíblia contradiz a hora de uma
cena). Os prompts em si são escritos pelo escritor de prompts (app/prompt_writer.py): a app não monta prompts."""

import re

TIME_WORDS = ("twilight", "dusk", "blue hour", "blue-hour", "night", "nighttime", "midnight", "midday", "noon", "daylight",
              "sunset", "sunrise", "dawn", "golden hour", "morning", "afternoon", "moonlight", "evening")


def time_words_in(text: str | None) -> list[str]:
    low = (text or "").lower()
    return [w for w in TIME_WORDS if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low)]
