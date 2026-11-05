"""Testy logiki oceny z ewaluacja.py.

Ocena musi przepuszczać poprawne odpowiedzi zapisane na różne sposoby,
ale nie może przepuszczać błędnych. Bez tych testów wynik ewaluacji
byłby niewiarygodny.

Uruchomienie:  pytest -v
"""
import json

import pytest

import ewaluacja


@pytest.mark.parametrize("odpowiedz, oczekiwane", [
    ("W bazie jest 3 503 utworów.", [3503]),
    ("Łączny przychód: 2 328,60 USD", [2328.6]),
    ("Total revenue is $2,328.60", [2328.6]),
    ("Średnia to 5,65.", [5.65]),
    ("Najwięcej: Stany Zjednoczone, 523,06", [["USA", "Stany Zjednoczone"], 523.06]),
    ("IRON MAIDEN ma 21 albumów", ["Iron Maiden", 21]),
])
def test_ocena_akceptuje_rozne_zapisy_poprawnej_odpowiedzi(odpowiedz, oczekiwane):
    assert ewaluacja.ocen(odpowiedz, oczekiwane) == (True, [])


@pytest.mark.parametrize("odpowiedz, oczekiwane", [
    ("Klientów z Brazylii: 59", [5]),  # 5 nie może "pasować" do 59
    ("Iron Maiden ma 20 albumów", ["Iron Maiden", 21]),
    ("W 2014 wystawiono 80 faktur.", [[0, "brak", "nie wystawiono"]]),
])
def test_ocena_odrzuca_bledne_odpowiedzi(odpowiedz, oczekiwane):
    poprawna, brakuje = ewaluacja.ocen(odpowiedz, oczekiwane)
    assert not poprawna
    assert brakuje


def test_wzorcowe_zapytania_daja_oczekiwane_wartosci():
    # "Test testów": każde pytanie z zestawu ma poprawne oczekiwania.
    pytania = json.loads(ewaluacja.PLIK_PYTAN.read_text(encoding="utf-8"))
    assert len(pytania) == 27
    assert ewaluacja.sprawdz_zestaw_pytan(pytania)


def test_numery_pytan_sa_unikalne():
    pytania = json.loads(ewaluacja.PLIK_PYTAN.read_text(encoding="utf-8"))
    numery = [p["id"] for p in pytania]
    assert len(numery) == len(set(numery))
