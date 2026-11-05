"""
Krok 2: pierwsze narzędzie (tool calling).

Co robi ten skrypt:
1. Opisuje modelowi jedno narzędzie: list_tables (lista tabel w bazie).
2. Zadaje pytanie o bazę. Model sam nie widzi bazy, więc PROSI o narzędzie.
3. Nasz kod uruchamia funkcję list_tables() i odsyła wynik modelowi.
4. Model, mając wynik, pisze odpowiedź końcową.

To jest cały "sekret" agentów: model decyduje, nasz kod wykonuje.

Uruchomienie (w aktywnym środowisku .venv-1):
    python krok2_narzedzie.py
"""

import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


# --- Ustawienia -------------------------------------------------------------
# Konkretna wersja modelu (nie alias "latest"), żeby wyniki były powtarzalne.
MODEL = "gemini-3.1-flash-lite"

# Ścieżka do bazy liczona względem tego pliku, więc działa niezależnie od tego,
# z którego folderu uruchomisz skrypt.
DB_PATH = Path(__file__).parent / "data" / "chinook.db"

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    sys.exit("Brak klucza! Sprawdź plik .env.")
if not DB_PATH.exists():
    sys.exit(f"Nie znalazłem bazy: {DB_PATH}\nPobierz Chinook i zapisz jako data/chinook.db")

client = genai.Client(api_key=api_key)


# --- 1. Narzędzie: zwykła funkcja w Pythonie --------------------------------
def list_tables() -> list[str]:
    """Zwraca nazwy wszystkich tabel w bazie."""
    # mode=ro = tylko do odczytu. Nawet gdyby coś poszło nie tak,
    # nie da się niczego w bazie zmienić ani usunąć.
    polaczenie = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        wiersze = polaczenie.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    finally:
        polaczenie.close()
    return [w[0] for w in wiersze]


# --- 2. Opis narzędzia dla modelu -------------------------------------------
# Model NIE widzi kodu funkcji. Widzi tylko nazwę i opis poniżej.
# Na tej podstawie decyduje, czy i kiedy poprosić o narzędzie,
# dlatego dobry opis jest tak samo ważny jak sam kod.
list_tables_opis = types.FunctionDeclaration(
    name="list_tables",
    description=(
        "Zwraca listę nazw wszystkich tabel w bazie danych sklepu muzycznego. "
        "Użyj, gdy musisz wiedzieć, jakie dane są dostępne."
    ),
)

konfiguracja = types.GenerateContentConfig(
    system_instruction=(
        "Jesteś asystentem, który odpowiada na pytania o bazę danych. "
        "Nie zgaduj: gdy potrzebujesz informacji o bazie, użyj narzędzia. "
        "Odpowiadaj po polsku."
    ),
    tools=[types.Tool(function_declarations=[list_tables_opis])],
    # Wyłączamy automatyczne wywoływanie: chcemy sami zobaczyć i wykonać
    # każdy krok, żeby rozumieć, co się dzieje.
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)

# Mapa: nazwa narzędzia -> funkcja w Pythonie.
# Gdy model poprosi o "list_tables", wiemy, którą funkcję uruchomić.
NARZEDZIA = {"list_tables": list_tables}


# --- 3. Rozmowa -------------------------------------------------------------
pytanie = "Jakie tabele są w bazie? Opisz krótko, do czego może służyć ta baza."
print(f"Pytanie: {pytanie}\n")

# "contents" to historia rozmowy. Za każdym razem wysyłamy CAŁĄ historię,
# bo model nie pamięta poprzednich zapytań. Pamięć to nasza lista.
historia = [types.Content(role="user", parts=[types.Part(text=pytanie)])]

# Pierwsze zapytanie: model dostaje pytanie i opis narzędzia.
odpowiedz = client.models.generate_content(
    model=MODEL, contents=historia, config=konfiguracja
)

if not odpowiedz.function_calls:
    # Model odpowiedział od razu, bez narzędzia (nie powinien, ale bywa).
    print("Model odpowiedział bez narzędzia:\n")
    print(odpowiedz.text)
    sys.exit()

# Dopisujemy do historii to, co powiedział model (czyli prośbę o narzędzie).
historia.append(odpowiedz.candidates[0].content)

# Model może poprosić o kilka narzędzi naraz, więc przechodzimy przez listę.
for wywolanie in odpowiedz.function_calls:
    print(f">> Model prosi o narzędzie: {wywolanie.name}({dict(wywolanie.args or {})})")

    funkcja = NARZEDZIA[wywolanie.name]
    wynik = funkcja(**(wywolanie.args or {}))
    print(f">> Wynik narzędzia: {wynik}\n")

    # Odsyłamy wynik modelowi jako "odpowiedź funkcji".
    historia.append(
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=wywolanie.id,
                        name=wywolanie.name,
                        response={"result": wynik},
                    )
                )
            ],
        )
    )

# Drugie zapytanie: model ma już wynik narzędzia i pisze odpowiedź końcową.
odpowiedz = client.models.generate_content(
    model=MODEL, contents=historia, config=konfiguracja
)

print("Odpowiedź modelu:\n")
print(odpowiedz.text)
