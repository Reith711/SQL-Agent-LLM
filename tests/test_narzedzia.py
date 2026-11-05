"""Testy narzędzi agenta.

Działają na prawdziwej bazie Chinook, ale bez wywoływania modelu,
więc są darmowe, szybkie i dają zawsze ten sam wynik.

Uruchomienie:  pytest -v
"""
import pytest

import agent


def test_list_tables_zwraca_wszystkie_11_tabel():
    tabele = agent.list_tables()
    assert len(tabele) == 11
    assert {"Customer", "Invoice", "Track"} <= set(tabele)


def test_describe_table_pokazuje_kolumny_i_klucze_obce():
    opis = agent.describe_table("Album")
    assert [k["name"] for k in opis["columns"]] == ["AlbumId", "Title", "ArtistId"]
    assert {"column": "ArtistId", "references": "Artist.ArtistId"} in opis["foreign_keys"]
    assert len(opis["sample_rows"]) == 3


def test_describe_table_odrzuca_nieistniejaca_tabele_i_wstrzykniecie_sql():
    assert "error" in agent.describe_table("NieMaTakiej")
    assert "error" in agent.describe_table('Album"); DROP TABLE Album; --')


def test_run_query_zwraca_kolumny_i_wiersze():
    wynik = agent.run_query("SELECT COUNT(*) AS liczba FROM Customer;")
    assert wynik == {"columns": ["liczba"], "rows": [[59]], "truncated": False}


@pytest.mark.parametrize("sql", [
    "DELETE FROM Customer",
    "DROP TABLE Customer",
    "UPDATE Customer SET FirstName = 'X'",
    "INSERT INTO Artist (Name) VALUES ('X')",
])
def test_run_query_blokuje_zapytania_modyfikujace(sql):
    assert "error" in agent.run_query(sql)


def test_run_query_blokuje_kilka_zapytan_naraz():
    assert "error" in agent.run_query("SELECT 1; DROP TABLE Customer")


def test_tryb_tylko_do_odczytu_zatrzymuje_sprytne_zapytanie():
    # "WITH ... DELETE" przechodzi sprawdzenie "zaczyna się od SELECT/WITH",
    # więc zatrzymać go musi dopiero druga warstwa: baza otwarta tylko do odczytu.
    wynik = agent.run_query("WITH x AS (SELECT 1) DELETE FROM Customer")
    assert "readonly" in wynik["error"]
    # Dane nietknięte:
    assert agent.run_query("SELECT COUNT(*) FROM Customer")["rows"] == [[59]]


def test_run_query_zwraca_blad_sql_zamiast_wywracac_program():
    # Agent dostaje błąd jako wynik narzędzia, żeby sam poprawił zapytanie.
    assert "no such table" in agent.run_query("SELECT * FROM NieMaTakiej")["error"]


def test_run_query_obcina_duze_wyniki():
    wynik = agent.run_query("SELECT TrackId FROM Track")
    assert len(wynik["rows"]) == agent.MAX_WIERSZY
    assert wynik["truncated"] is True


def test_nieznane_narzedzie_i_zle_argumenty_nie_wywracaja_agenta():
    assert "error" in agent._wykonaj_narzedzie("format_disk", {})
    assert "error" in agent._wykonaj_narzedzie("run_query", {"zly_argument": 1})


def test_opis_schematu_zawiera_wszystkie_tabele_i_relacje():
    opis = agent.opis_schematu()
    for tabela in agent.list_tables():
        assert f"{tabela}(" in opis
    assert "Track.GenreId -> Genre.GenreId" in opis
