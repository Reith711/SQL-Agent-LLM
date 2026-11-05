"""
Krok 1: pierwsza rozmowa z LLM (Gemini).

Co robi ten skrypt:
1. Wczytuje klucz API z pliku .env (żeby klucz nie był wpisany w kodzie).
2. Wypisuje modele dostępne dla Twojego klucza i wybiera model "flash-lite".
3. Wysyła do modelu jedno pytanie i drukuje odpowiedź.

Uruchomienie (w aktywnym środowisku .venv):
    python steps/krok1_test.py
"""

import os
import sys

from dotenv import load_dotenv  # biblioteka, która czyta plik .env
from google import genai        # oficjalna biblioteka Google do Gemini API


# --- 1. Klucz API -----------------------------------------------------------
# load_dotenv() wczytuje zmienne z pliku .env do "środowiska" programu.
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    sys.exit("Brak klucza! Utwórz plik .env z linijką: GEMINI_API_KEY=twój_klucz")

# Client to "połączenie" z API Google. Przez niego wysyłamy wszystkie zapytania.
client = genai.Client(api_key=api_key)


# --- 2. Lista modeli --------------------------------------------------------
print("Modele, które potrafią generować tekst:\n")

dostepne = []
for model in client.models.list():
    # Interesują nas tylko modele, które umieją odpowiadać na wiadomości
    # (akcja "generateContent"). Pomijamy np. modele do embeddingów czy obrazów.
    akcje = model.supported_actions or []
    if "generateContent" in akcje:
        nazwa = model.name.replace("models/", "")
        dostepne.append(nazwa)
        print("  -", nazwa)

# Wybieramy model z "flash-lite" w nazwie, bo ma najwyższy darmowy limit.
# Pomijamy wersje "preview", bo bywają niestabilne i mają niższe limity.
flash_lite = [n for n in dostepne if "flash-lite" in n and "preview" not in n]

if not flash_lite:
    sys.exit("\nNie znalazłem modelu flash-lite. Wklej Claude'owi listę powyżej.")

# Sortujemy, żeby wziąć najnowszą wersję (wyższy numer = nowszy model).
MODEL = sorted(flash_lite)[-1]
print(f"\nWybrany model: {MODEL}\n")


# --- 3. Pierwsze pytanie do modelu -----------------------------------------
# generate_content to najprostsze wywołanie: wysyłasz tekst, dostajesz tekst.
odpowiedz = client.models.generate_content(
    model=MODEL,
    contents="Wyjaśnij w dwóch zdaniach po polsku, czym jest agent AI.",
)

print("Odpowiedź modelu:\n")
print(odpowiedz.text)

# Informacja o zużytych tokenach — przyda się później do liczenia kosztów.
uzycie = odpowiedz.usage_metadata
if uzycie:
    print(f"\n(Tokeny: wejście {uzycie.prompt_token_count}, "
          f"wyjście {uzycie.candidates_token_count})")
