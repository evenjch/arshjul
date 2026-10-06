"""Brukere til årshjulet – fra kommandolinjen. Det finnes ingen registreringsside.

    python manage.py ny-bruker <brukernavn> "<Fullt navn>" ingenior|faglarer
    python manage.py passord <brukernavn>
    python manage.py deaktiver <brukernavn>      (kan ikke logge inn, men historikken står)
    python manage.py aktiver <brukernavn>        (også: opphever låsing etter feil passord)
    python manage.py liste

Passordet spørres om i terminalen (vises ikke). For skript på serveren kan det gis i
miljøvariabelen ARSHJUL_NYTT_PASSORD i stedet. Bruker samme innstillinger som appen
(ARSHJUL_DATA, innstillinger.env), så det er samme database.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sqlite3
import sys

import auth
import config
from db import Db


def _passord() -> str:
    pw = os.environ.get("ARSHJUL_NYTT_PASSORD")
    if pw:
        return pw
    while True:
        pw = getpass.getpass("Passord: ")
        feil = auth.passordfeil(pw)
        if feil:
            print(feil)
            continue
        if getpass.getpass("Gjenta passordet: ") != pw:
            print("Passordene er ikke like.")
            continue
        return pw


def _finn(store: Db, brukernavn: str) -> dict:
    u = store.bruker(brukernavn=brukernavn)
    if not u:
        sys.exit(f"Fant ingen bruker «{brukernavn}».")
    return u


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Brukere til årshjulet.")
    sub = p.add_subparsers(dest="kommando", required=True)
    ny = sub.add_parser("ny-bruker", help="lag en ny bruker")
    ny.add_argument("brukernavn")
    ny.add_argument("navn", help="fullt navn, slik det vises i årshjulet")
    ny.add_argument("rolle", choices=auth.ROLLER)
    for k in ("passord", "deaktiver", "aktiver"):
        sub.add_parser(k).add_argument("brukernavn")
    sub.add_parser("liste")
    a = p.parse_args(argv)

    store = Db(config.DB_FILE)
    store.init(config.DATA_FILE)

    if a.kommando == "ny-bruker":
        brukernavn = a.brukernavn.strip()
        if not brukernavn or any(c.isspace() for c in brukernavn):
            sys.exit("Brukernavnet kan ikke være tomt eller inneholde mellomrom.")
        try:
            hashet = auth.hash_passord(_passord())
        except ValueError as e:
            sys.exit(str(e))
        try:
            store.ny_bruker(brukernavn, a.navn.strip(), a.rolle, hashet)
        except sqlite3.IntegrityError:
            sys.exit(f"Brukernavnet «{brukernavn}» er tatt.")
        print(f"Laget {auth.ROLLENAVN[a.rolle].lower()} «{brukernavn}» ({a.navn}).")
    elif a.kommando == "passord":
        u = _finn(store, a.brukernavn)
        try:
            store.endre_bruker(u["id"], passord=auth.hash_passord(_passord()), feilforsok=0, last_til=None)
        except ValueError as e:
            sys.exit(str(e))
        print(f"Nytt passord for «{u['brukernavn']}». Økter med det gamle passordet er avsluttet.")
    elif a.kommando == "deaktiver":
        u = _finn(store, a.brukernavn)
        store.endre_bruker(u["id"], aktiv=0)
        print(f"«{u['brukernavn']}» kan ikke lenger logge inn.")
    elif a.kommando == "aktiver":
        u = _finn(store, a.brukernavn)
        store.endre_bruker(u["id"], aktiv=1, feilforsok=0, last_til=None)
        print(f"«{u['brukernavn']}» kan logge inn igjen.")
    else:
        brukere = store.brukere()
        if not brukere:
            print("Ingen brukere ennå. Lag en med: python manage.py ny-bruker <brukernavn> \"<Navn>\" ingenior")
        for u in brukere:
            status = "aktiv" if u["aktiv"] else "deaktivert"
            if u["last_til"]:
                status += f", låst til {u['last_til']}"
            print(f"{u['brukernavn']:<16} {u['navn']:<28} {auth.ROLLENAVN[u['rolle']]:<10} {status}"
                  f"   sist inne: {u['sist_inne'] or '–'}")


if __name__ == "__main__":
    main()
