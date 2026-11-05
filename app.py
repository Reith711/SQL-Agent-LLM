"""
Etap 4: okienko czatu w przeglądarce (Streamlit).

To jest "twarz" projektu dla pracownika, który nie zna SQL:
pole tekstowe, odpowiedzi i (dla ciekawskich) podgląd, jak agent
doszedł do odpowiedzi.

Cała logika agenta jest w agent.py. Tutaj tylko ją wywołujemy
i ładnie wyświetlamy wynik.

Uruchomienie (w aktywnym środowisku .venv-1):
    streamlit run app.py
"""

import pandas as pd
import streamlit as st
from google.genai import errors

import agent
from agent import MODEL, zapytaj_agenta

PRZYKLADOWE_PYTANIA = [
    "Ilu jest klientów w bazie?",
    "Którzy 3 klienci wydali najwięcej pieniędzy?",
    "Jaki gatunek muzyki przyniósł największy przychód?",
    "Który pracownik obsługi ma przypisanych najwięcej klientów?",
    "Ile wynosiła sprzedaż w poszczególnych krajach? Pokaż 5 najlepszych.",
]


# --- Ustawienia strony ------------------------------------------------------
st.set_page_config(page_title="Agent SQL", page_icon="🎵")
st.title("🎵 Asystent danych sklepu muzycznego")
st.caption(
    "Zadaj pytanie po polsku lub angielsku. Agent sam znajdzie dane w bazie, "
    "napisze zapytanie SQL i odpowie. Każde pytanie jest niezależne."
)


# --- Pamięć strony ----------------------------------------------------------
# Streamlit uruchamia ten plik OD NOWA po każdym kliknięciu czy wiadomości.
# Zwykłe zmienne by znikały, dlatego wiadomości trzymamy w st.session_state,
# które przetrwa między uruchomieniami (to jedyna "pamięć" tej strony).
if "wiadomosci" not in st.session_state:
    st.session_state.wiadomosci = []


# --- Panel boczny -----------------------------------------------------------
with st.sidebar:
    st.subheader("Przykładowe pytania")
    for pytanie in PRZYKLADOWE_PYTANIA:
        if st.button(pytanie, use_container_width=True):
            # Zapamiętujemy kliknięte pytanie; obsłużymy je niżej jak wpisane.
            st.session_state.oczekujace = pytanie

    st.divider()
    # Przełącznik trybu. Streamlit uruchamia plik od nowa przy każdej akcji,
    # więc ustawienie agenta odświeża się zgodnie z położeniem przełącznika.
    agent.SCHEMAT_W_PROMPCIE = st.toggle(
        "Tryb szybki (schemat bazy w prompcie)",
        help="Agent dostaje opis tabel od razu, zamiast odkrywać je narzędziami.",
    )
    st.caption(f"Model: `{MODEL}`")
    if st.button("Wyczyść rozmowę", use_container_width=True):
        st.session_state.wiadomosci = []
        st.rerun()


# --- Wyświetlanie jednej wiadomości -----------------------------------------
def pokaz_szczegoly(wynik: dict) -> None:
    """Rozwijany podgląd: jakie narzędzia wywołał agent i jaki SQL napisał."""
    kroki = wynik["kroki"]
    if not kroki:
        return

    with st.expander(f"Jak agent doszedł do odpowiedzi ({len(kroki)} kroków)"):
        for numer, krok in enumerate(kroki, start=1):
            nazwa = krok["narzedzie"]
            wynik_narzedzia = krok["wynik"]

            if nazwa == "run_query":
                st.markdown(f"**{numer}. Zapytanie SQL**")
                st.code(krok["argumenty"].get("sql", ""), language="sql")
                if isinstance(wynik_narzedzia, dict) and "rows" in wynik_narzedzia:
                    tabela = pd.DataFrame(
                        wynik_narzedzia["rows"], columns=wynik_narzedzia["columns"]
                    )
                    st.dataframe(tabela, hide_index=True)
            elif nazwa == "describe_table":
                st.markdown(f"**{numer}. Sprawdzenie tabeli** `{krok['argumenty'].get('table_name')}`")
            else:
                st.markdown(f"**{numer}. Lista tabel w bazie**")

            if isinstance(wynik_narzedzia, dict) and "error" in wynik_narzedzia:
                st.warning(wynik_narzedzia["error"])

        st.caption(
            f"Tokeny: {wynik['tokeny_wejscie']} wejście / {wynik['tokeny_wyjscie']} wyjście"
        )


def pokaz_wiadomosc(wiadomosc: dict) -> None:
    with st.chat_message(wiadomosc["rola"]):
        st.markdown(wiadomosc["tekst"])
        if wiadomosc.get("wynik"):
            pokaz_szczegoly(wiadomosc["wynik"])


# Najpierw rysujemy całą dotychczasową rozmowę.
for wiadomosc in st.session_state.wiadomosci:
    pokaz_wiadomosc(wiadomosc)


# --- Nowe pytanie -----------------------------------------------------------
# Pytanie przychodzi albo z pola tekstowego, albo z przycisku w panelu bocznym.
pytanie = st.chat_input("Np. Który artysta ma najwięcej albumów?")
if not pytanie:
    pytanie = st.session_state.pop("oczekujace", None)

if pytanie:
    wiadomosc_uzytkownika = {"rola": "user", "tekst": pytanie}
    st.session_state.wiadomosci.append(wiadomosc_uzytkownika)
    pokaz_wiadomosc(wiadomosc_uzytkownika)

    with st.chat_message("assistant"):
        with st.spinner("Agent przegląda bazę..."):
            try:
                wynik = zapytaj_agenta(pytanie, pokazuj_kroki=False)
                tekst = wynik["odpowiedz"]
            except errors.APIError as blad:
                # Np. przeciążony serwer mimo ponawiania. Nie wywracamy strony,
                # tylko pokazujemy czytelny komunikat.
                wynik = None
                tekst = f"Nie udało się połączyć z modelem ({blad.code}). Spróbuj za chwilę."

        st.markdown(tekst)
        if wynik:
            pokaz_szczegoly(wynik)

    st.session_state.wiadomosci.append({"rola": "assistant", "tekst": tekst, "wynik": wynik})
