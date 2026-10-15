"""Leitura de campos de formulário: texto vazio → None, inteiros validados."""


class FormError(ValueError):
    pass


def text(form, name: str) -> str | None:
    value = form.get(name)
    if not isinstance(value, str):
        return None
    value = value.strip().replace("\r\n", "\n")
    return value or None


def integer(form, name: str, label: str, minimum: int = 0) -> int | None:
    raw = text(form, name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise FormError(f"{label}: tem de ser um número inteiro.")
    if value < minimum:
        raise FormError(f"{label}: tem de ser pelo menos {minimum}.")
    return value


def lines(form, name: str) -> list[str]:
    raw = text(form, name) or ""
    return [line.strip() for line in raw.split("\n") if line.strip()]
