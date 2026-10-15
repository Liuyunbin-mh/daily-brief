"""Turn common LaTeX in abstracts into readable characters.

Feishu cards do not typeset formulas. This keeps the surrounding sentence readable.
"""

from __future__ import annotations

import re

_SYMBOLS = {
    "times": "×",
    "cdot": "·",
    "pm": "±",
    "mp": "∓",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "approx": "≈",
    "sim": "∼",
    "infty": "∞",
    "partial": "∂",
    "nabla": "∇",
    "sum": "∑",
    "prod": "∏",
    "int": "∫",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "theta": "θ",
    "lambda": "λ",
    "mu": "μ",
    "pi": "π",
    "sigma": "σ",
    "tau": "τ",
    "phi": "φ",
    "omega": "ω",
    "Delta": "Δ",
    "Sigma": "Σ",
    "Omega": "Ω",
    "ldots": "…",
    "cdots": "⋯",
    "quad": " ",
    "qquad": " ",
    "left": "",
    "right": "",
}

_SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUB = str.maketrans("0123456789+-=()aeiornx", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒᵣₙₓ")
_TEXT_COMMAND = re.compile(r"\\(?:textit|textbf|emph|text|mathrm|mathbf|operatorname)\{([^{}]*)\}")


def to_plain_math(text: str) -> str:
    if not text:
        return ""
    converted = _replace_delimited(text, r"\$\$(.+?)\$\$")
    converted = _replace_delimited(converted, r"\$(.+?)\$")
    converted = _replace_delimited(converted, r"\\\[(.+?)\\\]")
    converted = _replace_delimited(converted, r"\\\((.+?)\\\)")
    converted = _TEXT_COMMAND.sub(r"\1", converted)
    converted = re.sub(r"[ \t]{2,}", " ", converted)
    return converted.strip()


def _replace_delimited(text: str, pattern: str) -> str:
    return re.sub(pattern, lambda match: _tex(match.group(1)), text, flags=re.S)


def _tex(expr: str) -> str:
    expr = _TEXT_COMMAND.sub(r"\1", expr.strip())
    expr = expr.replace(r"\left", "").replace(r"\right", "")
    while True:
        match = re.search(r"\\frac\{", expr)
        if match is None:
            break
        numerator, after_num = _take_brace(expr, match.end() - 1)
        if after_num >= len(expr) or expr[after_num] != "{":
            break
        denominator, after_den = _take_brace(expr, after_num)
        expr = expr[: match.start()] + f"({_tex(numerator)})/({_tex(denominator)})" + expr[after_den:]
    expr = re.sub(r"\^\{([^{}]+)\}", lambda match: _script(match.group(1), _SUP), expr)
    expr = re.sub(r"\^([A-Za-z0-9])", lambda match: _script(match.group(1), _SUP), expr)
    expr = re.sub(r"_\{([^{}]+)\}", lambda match: _script(match.group(1), _SUB), expr)
    expr = re.sub(r"_([A-Za-z0-9])", lambda match: _script(match.group(1), _SUB), expr)

    def command(match: re.Match[str]) -> str:
        return _SYMBOLS.get(match.group(1), match.group(0))

    expr = re.sub(r"\\([A-Za-z]+)", command, expr)
    expr = expr.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", expr).strip()


def _take_brace(text: str, start: int) -> tuple[str, int]:
    if start >= len(text) or text[start] != "{":
        return "", start
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
    return text[start + 1 :], len(text)


def _script(text: str, table: dict[int, str]) -> str:
    return "".join(character.translate(table) for character in text)
