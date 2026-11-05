"""
Etap 3: pełny agent SQL.

Agent odpowiada na pytania o bazę Chinook (sklep muzyczny). Sam:
  1. sprawdza, jakie są tabele        -> narzędzie list_tables
  2. ogląda kolumny potrzebnych tabel -> narzędzie describe_table
  3. pisze i wykonuje zapytanie SQL   -> narzędzie run_query
  4. gdy SQL rzuci błąd, czyta go i poprawia zapytanie
  5. odpowiada zwykłym zdaniem

Nowości względem kroku 2:
  - PĘTLA: model wywołuje narzędzia tyle razy, ile potrzebuje (do MAX_KROKOW)
  - ZABEZPIECZENIA: baza tylko do odczytu, tylko SELECT, limit wierszy
  - RETRY: przy przeciążeniu serwera (503) czekamy i próbujemy ponownie
  - funkcja zapytaj_agenta() zwraca też statystyki (kroki, SQL, tokeny),
    które wykorzystamy w ewaluacji (etap 5) i w okienku czatu (etap 4)

Uruchomienie (w aktywnym środowisku .venv-1):
    python agent.py
"""

import os
import sqlite3
import sys
import time
from contextlib import closing  # gwarantuje zamknięcie połączenia z bazą
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types


# =============================================================================
# Ustawienia
# =============================================================================
MODEL = "gemini-3.1-flash-lite"
DB_PATH = Path(__file__).parent / "data" / "chinook.db"

MAX_KROKOW = 10      # ile razy agent może wywołać model, zanim się poddamy
MAX_WIERSZY = 50     # ile wierszy wyniku SQL maksymalnie oddajemy modelowi
MAX_PROB_API = 4     # ile razy ponawiamy zapytanie, gdy serwer jest przeciążony

load_dotenv()
_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    sys.exit("Brak klucza! Sprawdź plik .env.")
if not DB_PATH.exists():
    sys.exit(f"Nie znalazłem bazy: {DB_PATH}")

client = genai.Client(api_key=_api_key)


# =============================================================================
# Część 1: narzędzia (zwykłe funkcje w Pythonie)
# =============================================================================
def _polacz() -> sqlite3.Connection:
    """Otwiera bazę TYLKO DO ODCZYTU.

    To najważniejsze zabezpieczenie w całym projekcie. Nawet jeśli model
    wygeneruje DELETE albo DROP i przejdzie przez inne sprawdzenia,
    SQLite odmówi zapisu, bo samo połączenie na to nie pozwala.
    """
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def list_tables() -> list[str]:
    """Zwraca nazwy wszystkich tabel w bazie."""
    with closing(_polacz()) as polaczenie:
        wiersze = polaczenie.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    return [w[0] for w in wiersze]


def describe_table(table_name: str) -> dict:
    """Zwraca kolumny tabeli, klucze obce i 3 przykładowe wiersze."""
    # Nazwy tabeli nie da się przekazać do SQL jako parametru (?),
    # więc sprawdzamy ją z listą prawdziwych tabel. Dzięki temu model
    # nie może przemycić tu żadnego dodatkowego kodu SQL.
    if table_name not in list_tables():
        return {"error": f"Nie ma tabeli '{table_name}'. Dostępne: {list_tables()}"}

    with closing(_polacz()) as polaczenie:
        kolumny = polaczenie.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        klucze = polaczenie.execute(f'PRAGMA foreign_key_list("{table_name}")').fetchall()
        kursor = polaczenie.execute(f'SELECT * FROM "{table_name}" LIMIT 3')
        przyklady = [list(w) for w in kursor.fetchall()]

    return {
        "table": table_name,
        # PRAGMA table_info zwraca: (nr, nazwa, typ, not_null, domyślna, czy_klucz_główny)
        "columns": [
            {"name": k[1], "type": k[2], "primary_key": bool(k[5])} for k in kolumny
        ],
        # PRAGMA foreign_key_list zwraca m.in.: tabela docelowa (2), kolumna (3), kolumna docelowa (4)
        # To podpowiada modelowi, jak łączyć tabele przez JOIN.
        "foreign_keys": [
            {"column": k[3], "references": f"{k[2]}.{k[4]}"} for k in klucze
        ],
        "sample_rows": przyklady,
    }


def run_query(sql: str) -> dict:
    """Wykonuje zapytanie SELECT i zwraca wynik (maks. MAX_WIERSZY wierszy)."""
    zapytanie = sql.strip().rstrip(";").strip()

    # Sprawdzenia "na wejściu". Prawdziwą ochroną jest tryb tylko do odczytu,
    # ale te sprawdzenia dają modelowi czytelny komunikat zamiast błędu bazy.
    if not zapytanie.upper().startswith(("SELECT", "WITH")):
        return {"error": "Dozwolone są tylko zapytania SELECT (lub WITH ... SELECT)."}
    if ";" in zapytanie:
        return {"error": "Wykonuj jedno zapytanie naraz (bez średników w środku)."}

    try:
        with closing(_polacz()) as polaczenie:
            kursor = polaczenie.execute(zapytanie)
            kolumny = [opis[0] for opis in kursor.description]
            # Pobieramy o jeden wiersz więcej, żeby wiedzieć, czy wynik obcięliśmy.
            wiersze = kursor.fetchmany(MAX_WIERSZY + 1)
    except sqlite3.Error as blad:
        # Błąd NIE przerywa programu. Odsyłamy go modelowi,
        # żeby sam przeczytał, co jest nie tak, i poprawił zapytanie.
        return {"error": f"Błąd SQL: {blad}"}

    obciete = len(wiersze) > MAX_WIERSZY
    return {
        "columns": kolumny,
        "rows": [list(w) for w in wiersze[:MAX_WIERSZY]],
        "truncated": obciete,
    }


# Mapa: nazwa narzędzia -> funkcja w Pythonie
NARZEDZIA = {
    "list_tables": list_tables,
    "describe_table": describe_table,
    "run_query": run_query,
}


# =============================================================================
# Część 2: opisy narzędzi dla modelu
# =============================================================================
# Model widzi tylko to, co tu napiszemy. Dobre opisy = lepsze decyzje agenta.
OPISY_NARZEDZI = [
    types.FunctionDeclaration(
        name="list_tables",
        description="Zwraca listę nazw wszystkich tabel w bazie. Używaj na początku.",
    ),
    types.FunctionDeclaration(
        name="describe_table",
        description=(
            "Zwraca kolumny tabeli (nazwy i typy), klucze obce (jak łączyć tabele "
            "przez JOIN) oraz 3 przykładowe wiersze. Używaj przed pisaniem SQL."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "table_name": types.Schema(
                    type=types.Type.STRING,
                    description="Dokładna nazwa tabeli, np. 'Invoice'.",
                )
            },
            required=["table_name"],
        ),
    ),
    types.FunctionDeclaration(
        name="run_query",
        description=(
            f"Wykonuje JEDNO zapytanie SELECT w SQLite i zwraca maks. {MAX_WIERSZY} "
            "wierszy. Gdy zwróci błąd, przeczytaj go, popraw zapytanie i spróbuj ponownie."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "sql": types.Schema(
                    type=types.Type.STRING,
                    description="Zapytanie SQL w dialekcie SQLite.",
                )
            },
            required=["sql"],
        ),
    ),
]

INSTRUKCJA_SYSTEMOWA = """\
Jesteś asystentem danych sklepu z muzyką cyfrową. Odpowiadasz na pytania
pracowników, którzy nie znają SQL, korzystając z bazy SQLite.

Sposób pracy:
1. Jeśli nie znasz struktury bazy, użyj list_tables, a potem describe_table
   dla tabel, które mogą być potrzebne.
2. Napisz zapytanie SELECT (dialekt SQLite, np. daty przez strftime) i wykonaj
   je przez run_query.
3. Jeśli run_query zwróci błąd, przeczytaj go, popraw zapytanie i spróbuj znowu.
4. Odpowiedz krótko, w języku pytania, podając konkretne liczby i nazwy z wyniku.

Zasady:
- Nigdy nie zgaduj danych. Każdą liczbę w odpowiedzi musisz mieć z run_query.
- Nie próbuj zmieniać danych (żadnych INSERT, UPDATE, DELETE).
- Jeśli pytanie nie dotyczy bazy albo baza nie ma takich danych, powiedz to wprost.
"""

KONFIGURACJA = types.GenerateContentConfig(
    system_instruction=INSTRUKCJA_SYSTEMOWA,
    tools=[types.Tool(function_declarations=OPISY_NARZEDZI)],
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)


# =============================================================================
# Część 3: wywołanie modelu z ponawianiem (retry)
# =============================================================================
def _zapytaj_model(historia: list) -> types.GenerateContentResponse:
    """Wywołuje model. Przy przeciążeniu serwera czeka i próbuje ponownie.

    Odstęp rośnie za każdym razem (2 s, 4 s, 8 s...). To się nazywa
    "exponential backoff": nie zasypujemy przeciążonego serwera zapytaniami.
    """
    for proba in range(1, MAX_PROB_API + 1):
        try:
            return client.models.generate_content(
                model=MODEL, contents=historia, config=KONFIGURACJA
            )
        except errors.APIError as blad:
            # 429 = przekroczony limit, 500/503 = problem po stronie serwera.
            # Inne błędy (np. 400 = złe zapytanie) nie miną od czekania.
            if blad.code not in (429, 500, 503) or proba == MAX_PROB_API:
                raise
            czekaj = 2 ** proba
            print(f"   (serwer zwrócił {blad.code}, ponawiam za {czekaj} s...)")
            time.sleep(czekaj)


# =============================================================================
# Część 4: pętla agenta
# =============================================================================
def _wykonaj_narzedzie(nazwa: str, argumenty: dict):
    """Uruchamia narzędzie, o które poprosił model. Błąd zwraca, a nie rzuca."""
    funkcja = NARZEDZIA.get(nazwa)
    if funkcja is None:
        return {"error": f"Nie ma narzędzia '{nazwa}'."}
    try:
        return funkcja(**argumenty)
    except TypeError as blad:  # np. model podał złe argumenty
        return {"error": f"Złe argumenty dla {nazwa}: {blad}"}


def zapytaj_agenta(pytanie: str, pokazuj_kroki: bool = True) -> dict:
    """Zadaje agentowi pytanie i zwraca odpowiedź razem ze statystykami."""
    historia = [types.Content(role="user", parts=[types.Part(text=pytanie)])]
    kroki = []                  # co robił agent (przyda się w ewaluacji)
    tokeny_we = tokeny_wy = 0   # zużycie tokenów (koszt)

    for numer_kroku in range(1, MAX_KROKOW + 1):
        odpowiedz = _zapytaj_model(historia)

        if odpowiedz.usage_metadata:
            tokeny_we += odpowiedz.usage_metadata.prompt_token_count or 0
            tokeny_wy += odpowiedz.usage_metadata.candidates_token_count or 0

        if not odpowiedz.candidates:
            tekst = "Model nie zwrócił odpowiedzi (mogła zostać zablokowana)."
            break

        # Brak prośby o narzędzie = model skończył i to jest odpowiedź końcowa.
        if not odpowiedz.function_calls:
            tekst = odpowiedz.text or "(pusta odpowiedź)"
            break

        historia.append(odpowiedz.candidates[0].content)

        # Model może poprosić o kilka narzędzi naraz. Wszystkie wyniki
        # odsyłamy razem, w jednej wiadomości.
        wyniki = []
        for wywolanie in odpowiedz.function_calls:
            argumenty = dict(wywolanie.args or {})
            wynik = _wykonaj_narzedzie(wywolanie.name, argumenty)
            kroki.append({"narzedzie": wywolanie.name, "argumenty": argumenty, "wynik": wynik})

            if pokazuj_kroki:
                print(f"   [krok {numer_kroku}] {wywolanie.name}({argumenty})")
                if isinstance(wynik, dict) and "error" in wynik:
                    print(f"      ! {wynik['error']}")

            wyniki.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=wywolanie.id, name=wywolanie.name, response={"result": wynik}
                    )
                )
            )

        historia.append(types.Content(role="user", parts=wyniki))
    else:
        # Pętla "for" doszła do końca bez "break", czyli skończyły się kroki.
        tekst = f"Nie udało mi się odpowiedzieć w limicie {MAX_KROKOW} kroków."

    return {
        "odpowiedz": tekst,
        "kroki": kroki,
        "sql": [k["argumenty"].get("sql") for k in kroki if k["narzedzie"] == "run_query"],
        "tokeny_wejscie": tokeny_we,
        "tokeny_wyjscie": tokeny_wy,
    }


# =============================================================================
# Część 5: czat w terminalu
# =============================================================================
# Ten blok wykona się tylko przy "python agent.py". Gdy w etapie 5 zrobimy
# "from agent import zapytaj_agenta", czat się nie uruchomi.
if __name__ == "__main__":
    print(f"Agent SQL (model: {MODEL}). Wpisz pytanie albo 'exit', żeby wyjść.\n")
    while True:
        try:
            pytanie = input("Ty: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if pytanie.lower() in ("", "exit", "quit", "q"):
            break

        wynik = zapytaj_agenta(pytanie)
        print(f"\nAgent: {wynik['odpowiedz']}")
        print(
            f"\n(kroki: {len(wynik['kroki'])}, "
            f"tokeny: {wynik['tokeny_wejscie']} we / {wynik['tokeny_wyjscie']} wy)\n"
        )
