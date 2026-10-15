"""Comandos de administração: python -m app.cli <comando>"""

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path


def cmd_hash_password(_args) -> None:
    from app.security import MIN_PASSWORD_LENGTH, hash_password

    pw = getpass.getpass("Password: ")
    if len(pw) < MIN_PASSWORD_LENGTH:
        sys.exit(f"A password tem de ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
    if pw != getpass.getpass("Repetir password: "):
        sys.exit("As passwords não coincidem.")
    print(f"ADMIN_INITIAL_PASSWORD_HASH='{hash_password(pw)}'")


def cmd_init(_args) -> None:
    from sqlmodel import Session

    from app.db import engine
    from app.security import ensure_credential
    from app.seeds import run_seeds

    with Session(engine) as session:
        run_seeds(session)
        ensure_credential(session)
    print("Seeds e credencial verificados.")


def cmd_reset_password(_args) -> None:
    """Repõe a password inicial do .env (e o email) e obriga a mudar no próximo login."""
    from sqlmodel import Session

    from app.config import settings
    from app.db import engine
    from app.security import ensure_credential, get_credential

    with Session(engine) as session:
        ensure_credential(session)
        cred = get_credential(session)
        cred.email = settings.admin_email.strip().lower()
        cred.password_hash = settings.admin_initial_password_hash
        cred.must_change_password = True
        cred.session_version += 1
        session.add(cred)
        session.commit()
    print("Password reposta para a inicial do .env. Terás de a alterar no próximo login.")


def cmd_backup_db(args) -> None:
    """Cópia consistente da base de dados SQLite (seguro com a app a correr)."""
    from app.config import settings

    src_path = Path(settings.database_url.removeprefix("sqlite:///"))
    dest = Path(args.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    tmp.replace(dest)
    print(f"Base de dados copiada para {dest}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash-password", help="Gera o hash argon2 de uma password").set_defaults(func=cmd_hash_password)
    sub.add_parser("init", help="Aplica seeds e cria a credencial inicial").set_defaults(func=cmd_init)
    sub.add_parser("reset-password", help="Repõe a password inicial do .env").set_defaults(func=cmd_reset_password)
    p = sub.add_parser("backup-db", help="Copia a base de dados para um ficheiro")
    p.add_argument("dest")
    p.set_defaults(func=cmd_backup_db)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
