"""
Etap 5: ewaluacja agenta.

Sprawdza, jak dobrze agent odpowiada na 15 pytań o znanych odpowiedziach
(eval/pytania.json) i liczy wynik, np. "13/15 poprawnych".

Jak działa ocena:
  - każde pytanie ma listę "oczekiwane": liczby i nazwy, które MUSZĄ pojawić
    się w odpowiedzi agenta (np. ["Iron Maiden", 21]),
  - liczby porównujemy liczbowo (49,62 = 49.62 = "49.62 USD"),
    z tolerancją na zaokrąglenie do 2 miejsc po przecinku,
  - nazwy porównujemy bez względu na wielkość liter,
  - lista w liście oznacza "dowolny z wariantów", np. ["USA", "Stany Zjednoczone"].

Każde pytanie ma też "sql_wzorcowe" (poprawne zapytanie napisane ręcznie).
Przed startem skrypt wykonuje te zapytania i sprawdza, czy oczekiwane wartości
naprawdę wychodzą z bazy. To "test testów": błąd w zestawie pytań wyjdzie,
zanim zmarnujemy limit API.

Uruchomienie (w aktywnym środowisku .venv-1):
    python ewaluacja.py                    # tryb "narzędzia", model z agent.py
    python ewaluacja.py --schemat          # tryb "schemat" (schemat w prompcie)
    python ewaluacja.py --poziom trudne    # tylko wybrany poziom (oszczędza limit)
    python ewaluacja.py --model gemini-2.5-flash-lite   # inny model
    python ewaluacja.py --przerwa 6        # własna przerwa między pytaniami
    python ewaluacja.py --id 21 27         # tylko wybrane pytania (np. powtórka)

Wyniki zapisują się w eval/wyniki/ jako plik JSON.
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from google.genai import errors

import agent

KATALOG = Path(__file__).parent
PLIK_PYTAN = KATALOG / "eval" / "pytania.json"
KATALOG_WYNIKOW = KATALOG / "eval" / "wyniki"

# Domyślna przerwa między pytaniami. Darmowy plan ma limit zapytań na minutę.
# W trybie "narzędzia" jedno pytanie to 3-5 zapytań do modelu, w trybie
# "schemat" zwykle 1-2, więc tam przerwa może być krótsza.
PRZERWA_NARZEDZIA_S = 10
PRZERWA_SCHEMAT_S = 4

# Ile może różnić się liczba w odpowiedzi od oczekiwanej (zaokrąglenia).
TOLERANCJA = 0.006


# =============================================================================
# Część 1: porównywanie odpowiedzi z oczekiwaniami
# =============================================================================
def normalizuj_liczby(tekst: str) -> str:
    """Ujednolica zapis liczb: '2 328,60' i '2,328.60' -> '2328.60'."""
    tekst = tekst.replace(" ", " ").replace(" ", " ")  # twarde spacje
    # Separator tysięcy: spacja albo przecinek, po którym są dokładnie 3 cyfry.
    tekst = re.sub(r"(?<=\d)[ ,](?=\d{3}(?!\d))", "", tekst)
    # Przecinek dziesiętny (zapis polski) -> kropka.
    tekst = re.sub(r"(?<=\d),(?=\d)", ".", tekst)
    return tekst


def liczby_w_tekscie(tekst: str) -> list[float]:
    """Wyciąga wszystkie liczby z tekstu."""
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", normalizuj_liczby(tekst))]


def pasuje(tekst: str, liczby: list[float], oczekiwana) -> bool:
    """Czy jedna oczekiwana wartość występuje w tekście."""
    if isinstance(oczekiwana, list):  # dowolny z wariantów
        return any(pasuje(tekst, liczby, wariant) for wariant in oczekiwana)
    if isinstance(oczekiwana, (int, float)):
        return any(abs(liczba - oczekiwana) <= TOLERANCJA for liczba in liczby)
    return oczekiwana.lower() in tekst.lower()


def ocen(odpowiedz: str, oczekiwane: list) -> tuple[bool, list]:
    """Zwraca (czy_poprawna, lista_brakujących_wartości)."""
    liczby = liczby_w_tekscie(odpowiedz)
    brakuje = [o for o in oczekiwane if not pasuje(odpowiedz, liczby, o)]
    return (not brakuje, brakuje)


# =============================================================================
# Część 2: "test testów" - czy wzorcowe zapytania dają oczekiwane wartości
# =============================================================================
def sprawdz_zestaw_pytan(pytania: list) -> bool:
    poprawny = True
    with closing(sqlite3.connect(f"file:{agent.DB_PATH}?mode=ro", uri=True)) as baza:
        for p in pytania:
            wiersze = baza.execute(p["sql_wzorcowe"]).fetchall()
            tekst = " ".join(str(wartosc) for wiersz in wiersze for wartosc in wiersz)
            ok, brakuje = ocen(tekst, p["oczekiwane"])
            if not ok:
                poprawny = False
                print(f"  ! Pytanie {p['id']}: baza zwraca {wiersze}, a oczekujemy {brakuje}")
    return poprawny


# =============================================================================
# Część 3: przebieg ewaluacji
# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Ewaluacja agenta SQL")
    parser.add_argument("--model", help="nazwa modelu (domyślnie z agent.py)")
    parser.add_argument("--schemat", action="store_true",
                        help="tryb 'schemat': opis bazy w prompcie zamiast odkrywania narzędziami")
    parser.add_argument("--poziom", help="uruchom tylko pytania z tego poziomu, np. trudne")
    parser.add_argument("--przerwa", type=float, help="przerwa między pytaniami w sekundach")
    parser.add_argument("--id", type=int, nargs="+", help="uruchom tylko pytania o tych numerach")
    args = parser.parse_args()

    # agent.zapytaj_agenta czyta te ustawienia przy każdym wywołaniu,
    # więc podmiana tutaj zmienia je dla całej ewaluacji.
    if args.model:
        agent.MODEL = args.model
    agent.SCHEMAT_W_PROMPCIE = args.schemat
    tryb = "schemat" if args.schemat else "narzedzia"
    przerwa = args.przerwa if args.przerwa is not None else (
        PRZERWA_SCHEMAT_S if args.schemat else PRZERWA_NARZEDZIA_S
    )

    pytania = json.loads(PLIK_PYTAN.read_text(encoding="utf-8"))
    if args.poziom:
        pytania = [p for p in pytania if p["poziom"] == args.poziom]
        if not pytania:
            sys.exit(f"Brak pytań z poziomu '{args.poziom}'.")
    if args.id:
        pytania = [p for p in pytania if p["id"] in args.id]

    print("Sprawdzam zestaw pytań na bazie...")
    if not sprawdz_zestaw_pytan(pytania):
        sys.exit("Zestaw pytań ma błędy - popraw eval/pytania.json.")
    print(f"OK. Start ewaluacji: {len(pytania)} pytań, model {agent.MODEL}, tryb {tryb}\n")

    wyniki = []
    start = time.time()

    for numer, p in enumerate(pytania, start=1):
        print(f"[{numer:>2}/{len(pytania)}] ({p['poziom']}) {p['pytanie']}")
        t0 = time.time()
        blad_api = False
        try:
            odp = agent.zapytaj_agenta(p["pytanie"], pokazuj_kroki=False)
        except errors.APIError as blad:
            # Błąd API (np. limit) to nie pomyłka agenta, tylko brak odpowiedzi.
            # Liczymy go osobno, żeby nie zaniżać oceny jakości agenta.
            blad_api = True
            odp = {"odpowiedz": f"BŁĄD API {blad.code}", "kroki": [], "sql": [],
                   "tokeny_wejscie": 0, "tokeny_wyjscie": 0}

        ok, brakuje = ocen(odp["odpowiedz"], p["oczekiwane"])
        # Ile razy agent dostał błąd SQL (i musiał poprawić zapytanie).
        bledy_sql = sum(
            1 for k in odp["kroki"]
            if k["narzedzie"] == "run_query" and isinstance(k["wynik"], dict) and "error" in k["wynik"]
        )

        status = "⚠️  błąd API" if blad_api else ("✅ poprawna" if ok else "❌ błędna")
        print(f"        {status}  "
              f"(kroki: {len(odp['kroki'])}, błędy SQL: {bledy_sql}, {time.time() - t0:.1f} s)")
        if not ok and not blad_api:
            print(f"        brakuje: {brakuje}")
            print(f"        odpowiedź: {odp['odpowiedz'][:200]}")

        wyniki.append({
            **p,
            "poprawna": ok,
            "blad_api": blad_api,
            "brakuje": brakuje,
            "odpowiedz": odp["odpowiedz"],
            "sql_agenta": odp["sql"],
            "kroki": len(odp["kroki"]),
            "bledy_sql": bledy_sql,
            "tokeny_wejscie": odp["tokeny_wejscie"],
            "tokeny_wyjscie": odp["tokeny_wyjscie"],
        })

        if numer < len(pytania):
            time.sleep(przerwa)

    # --- Podsumowanie ---------------------------------------------------------
    n = len(wyniki)
    poprawne = sum(w["poprawna"] for w in wyniki)
    bledy_api = sum(w["blad_api"] for w in wyniki)
    ocenione = n - bledy_api  # pytania, na które agent faktycznie odpowiedział
    podsumowanie = {
        "model": agent.MODEL,
        "tryb": tryb,
        "poziom": args.poziom or "wszystkie",
        "data": datetime.now().isoformat(timespec="seconds"),
        "poprawne": poprawne,
        "wszystkie": n,
        "skutecznosc_proc": round(100 * poprawne / n, 1),
        "bledy_api": bledy_api,
        "skutecznosc_bez_bledow_api_proc": round(100 * poprawne / ocenione, 1) if ocenione else None,
        "wedlug_poziomu": {},
        "srednio_krokow": round(sum(w["kroki"] for w in wyniki) / n, 1),
        "srednio_tokenow_wejscie": round(sum(w["tokeny_wejscie"] for w in wyniki) / n),
        "srednio_tokenow_wyjscie": round(sum(w["tokeny_wyjscie"] for w in wyniki) / n),
        "pytania_z_samonaprawa_sql": sum(1 for w in wyniki if w["bledy_sql"] and w["poprawna"]),
        "czas_s": round(time.time() - start),
    }
    for poziom in dict.fromkeys(w["poziom"] for w in wyniki):  # kolejność jak w pliku
        grupa = [w for w in wyniki if w["poziom"] == poziom]
        podsumowanie["wedlug_poziomu"][poziom] = f"{sum(w['poprawna'] for w in grupa)}/{len(grupa)}"

    print("\n" + "=" * 60)
    print(f"WYNIK: {poprawne}/{n} poprawnych ({podsumowanie['skutecznosc_proc']}%)  "
          f"model: {agent.MODEL}, tryb: {tryb}")
    if bledy_api:
        print(f"Błędy API: {bledy_api}. Bez nich: {poprawne}/{ocenione} "
              f"({podsumowanie['skutecznosc_bez_bledow_api_proc']}%) - powtórz je opcją --id")
    print(f"Według poziomu: {podsumowanie['wedlug_poziomu']}")
    print(f"Czas: {podsumowanie['czas_s']} s.  Średnio: {podsumowanie['srednio_krokow']} kroków, "
          f"{podsumowanie['srednio_tokenow_wejscie']} tokenów wejściowych na pytanie")
    print(f"Pytania, w których agent sam naprawił błąd SQL: {podsumowanie['pytania_z_samonaprawa_sql']}")
    print("=" * 60)

    KATALOG_WYNIKOW.mkdir(parents=True, exist_ok=True)
    nazwa = f"eval_{agent.MODEL}_{tryb}_{datetime.now():%Y%m%d-%H%M}.json"
    (KATALOG_WYNIKOW / nazwa).write_text(
        json.dumps({"podsumowanie": podsumowanie, "wyniki": wyniki}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Szczegóły zapisane w eval/wyniki/{nazwa}")


if __name__ == "__main__":
    main()
