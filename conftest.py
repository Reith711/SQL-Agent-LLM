"""Wspólne ustawienia testów (pytest wczytuje ten plik automatycznie).

Testy sprawdzają narzędzia agenta i logikę oceny, ale NIE łączą się z modelem:
są darmowe i działają bez internetu. agent.py wymaga jednak klucza przy imporcie,
więc gdy prawdziwego klucza nie ma (np. na GitHub Actions), podstawiamy fałszywy.
"""
import os

os.environ.setdefault("GEMINI_API_KEY", "klucz-testowy")
