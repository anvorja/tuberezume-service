"""Gestión de usuarios desde la terminal.

  uv run python -m app.cli crear-usuario NOMBRE --rol admin|user
  uv run python -m app.cli cambiar-clave NOMBRE

La contraseña se pide por teclado (no queda en el historial) o, para scripts,
se lee de la variable de entorno indicada en --clave-env.
"""

import argparse
import getpass
import os
import sys

from . import db, security


def _password(args) -> str:
    if args.clave_env:
        v = os.environ.get(args.clave_env, "")
        if not v:
            sys.exit(f"La variable {args.clave_env} está vacía.")
        return v
    a, b = getpass.getpass("Contraseña: "), getpass.getpass("Repítela: ")
    if a != b:
        sys.exit("Las contraseñas no coinciden.")
    return a


def main() -> None:
    p = argparse.ArgumentParser(prog="app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crear-usuario")
    c.add_argument("nombre")
    c.add_argument("--rol", choices=["admin", "user"], default="user")
    c.add_argument("--clave-env")
    k = sub.add_parser("cambiar-clave")
    k.add_argument("nombre")
    k.add_argument("--clave-env")
    args = p.parse_args()

    db.init_db()
    if args.cmd == "crear-usuario":
        if db.get_user_by_name(args.nombre):
            sys.exit("Ese usuario ya existe.")
        pw = _password(args)
        if len(pw) < 8:
            sys.exit("La contraseña debe tener al menos 8 caracteres.")
        u = db.create_user(args.nombre, security.hash_password(pw), args.rol)
        extra = ""
        if u.role == "user":
            n = db.claim_orphans(u.id)
            extra = f" ({n} resúmenes anteriores asignados)" if n else ""
        print(f"Usuario «{u.username}» creado con rol {u.role}.{extra}")
    else:
        u = db.get_user_by_name(args.nombre)
        if not u:
            sys.exit("No existe ese usuario.")
        pw = _password(args)
        if len(pw) < 8:
            sys.exit("La contraseña debe tener al menos 8 caracteres.")
        db.update_user(u.id, security.hash_password(pw))
        print(f"Contraseña de «{u.username}» actualizada.")


if __name__ == "__main__":
    main()
