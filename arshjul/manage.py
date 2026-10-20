"""Users for the app – from the command line. There is no sign-up page.

    python manage.py ny-bruker <brukernavn> "<Fullt navn>" ingenior|faglarer [--admin]
    python manage.py admin <brukernavn> [--fjern]  (make an engineer admin, or not)
    python manage.py passord <brukernavn>
    python manage.py deaktiver <brukernavn>      (can't log in, but the history stays)
    python manage.py aktiver <brukernavn>        (also: lifts a lock-out after wrong passwords)
    python manage.py liste

The password is asked for in the terminal (not shown). For scripts on the server it can be given in
the environment variable ARSHJUL_NYTT_PASSORD instead. Uses the same settings as the app
(ARSHJUL_DATA, innstillinger.env), so it's the same database.
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


def _password() -> str:
    pw = os.environ.get("ARSHJUL_NYTT_PASSORD")
    if pw:
        return pw
    while True:
        pw = getpass.getpass("Passord: ")
        error = auth.password_error(pw)
        if error:
            print(error)
            continue
        if getpass.getpass("Gjenta passordet: ") != pw:
            print("Passordene er ikke like.")
            continue
        return pw


def _find(store: Db, username: str) -> dict:
    u = store.user(username=username)
    if not u:
        sys.exit(f"Fant ingen bruker «{username}».")
    return u


CLI_COMMANDS = {"ny-bruker": "new-user", "passord": "password", "deaktiver": "deactivate", "aktiver": "activate",
                "liste": "list"}
CLI_ROLES = {"ingenior": "engineer", "faglarer": "lecturer"}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Brukere til årshjulet.")
    sub = p.add_subparsers(dest="command", required=True)
    # the command line is what admins type, so it stays Norwegian; the code uses English names
    new = sub.add_parser("ny-bruker", help="lag en ny bruker")
    new.add_argument("username", metavar="brukernavn")
    new.add_argument("name", metavar="navn", help="fullt navn, slik det vises i årshjulet")
    new.add_argument("role", metavar="rolle", choices=CLI_ROLES, help="ingenior eller faglarer")
    new.add_argument("--admin", action="store_true", help="administrator (bare for ingeniører)")
    for k in ("passord", "deaktiver", "aktiver"):
        sub.add_parser(k).add_argument("username", metavar="brukernavn")
    adm = sub.add_parser("admin", help="gjør en ingeniør til administrator")
    adm.add_argument("username", metavar="brukernavn")
    adm.add_argument("--fjern", dest="remove", action="store_true", help="ta bort administratorrollen")
    sub.add_parser("liste")
    a = p.parse_args(argv)
    a.command = CLI_COMMANDS.get(a.command, a.command)
    if a.command == "new-user":
        a.role = CLI_ROLES[a.role]

    store = Db(config.DB_FILE)
    store.init(config.DATA_FILE)

    if a.command == "new-user":
        username = a.username.strip()
        if not username or any(c.isspace() for c in username):
            sys.exit("Brukernavnet kan ikke være tomt eller inneholde mellomrom.")
        if a.admin and a.role != "engineer":
            sys.exit("Bare ingeniører kan være administrator.")
        try:
            hashed = auth.hash_password(_password())
        except ValueError as e:
            sys.exit(str(e))
        try:
            store.new_user(username, a.name.strip(), a.role, hashed, admin=a.admin)
        except sqlite3.IntegrityError:
            sys.exit(f"Brukernavnet «{username}» er tatt.")
        print(f"Laget {auth.ROLE_NAMES[a.role].lower()}{' (administrator)' if a.admin else ''} «{username}» ({a.name}).")
    elif a.command == "admin":
        u = _find(store, a.username)
        if u["role"] != "engineer":
            sys.exit("Bare ingeniører kan være administrator.")
        if a.remove and u["admin"] and store.active_admins() <= 1:
            sys.exit("Det må finnes minst én aktiv administrator.")
        store.update_user(u["id"], admin=0 if a.remove else 1)
        print(f"«{u['username']}» er {'ikke lenger' if a.remove else 'nå'} administrator.")
    elif a.command == "password":
        u = _find(store, a.username)
        try:
            store.update_user(u["id"], password=auth.hash_password(_password()), failed_attempts=0, locked_until=None)
        except ValueError as e:
            sys.exit(str(e))
        print(f"Nytt passord for «{u['username']}». Økter med det gamle passordet er avsluttet.")
    elif a.command == "deactivate":
        u = _find(store, a.username)
        store.update_user(u["id"], active=0)
        print(f"«{u['username']}» kan ikke lenger logge inn.")
    elif a.command == "activate":
        u = _find(store, a.username)
        store.update_user(u["id"], active=1, failed_attempts=0, locked_until=None)
        print(f"«{u['username']}» kan logge inn igjen.")
    else:
        users = store.users()
        if not users:
            print("Ingen brukere ennå. Lag administratoren med: python manage.py ny-bruker <brukernavn> \"<Navn>\" ingenior --admin")
        for u in users:
            status = "aktiv" if u["active"] else "deaktivert"  # printed for the admin
            if u["locked_until"]:
                status += f", låst til {u['locked_until']}"
            role = auth.ROLE_NAMES[u["role"]] + (" (admin)" if u["admin"] else "")
            print(f"{u['username']:<16} {u['name']:<28} {role:<18} {status}"
                  f"   sist inne: {u['last_login'] or '–'}")


if __name__ == "__main__":
    main()
