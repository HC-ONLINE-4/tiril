#!/usr/bin/env python3
"""
Exporta cookies de TikTok desde Microsoft Edge.

Lee la base de datos de cookies de Edge y las descifra.
Si Edge esta abierto, pide cerrarlo (o puedes exportar manualmente).

Uso:
  python export_cookies.py              # exporta automaticamente
  python export_cookies.py --verify     # verifica un cookies.json existente
  python export_cookies.py --print      # imprime JSON en pantalla

Despues de exportar:
  1. Abre GitHub → tu repo → Settings → Secrets → Actions
  2. New repository secret
  3. Name: TIKTOK_COOKIES
  4. Value: pega el contenido de cookies.json
"""
import argparse
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile


def is_edge_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq msedge.exe"],
                           capture_output=True, text=True)
        return "msedge.exe" in r.stdout
    except Exception:
        return False


def get_edge_encryption_key():
    local_state = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "Edge", "User Data", "Local State"
    )
    with open(local_state, "r", encoding="utf-8") as f:
        state = json.load(f)
    key = base64.b64decode(state["os_crypt"]["encrypted_key"])[5:]
    import win32crypt
    return win32crypt.CryptUnprotectData(key, None, None, None, 0)[1]


def decrypt_cookie(value, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if value[:3] in (b"v10", b"v20"):
        iv = value[3:15]
        tag = value[-16:]
        aes = AESGCM(key)
        return aes.decrypt(iv, value[15:-16], None).decode("utf-8")
    import win32crypt
    return win32crypt.CryptUnprotectData(value, None, None, None, 0)[1].decode("utf-8")


def read_cookies_from_edge():
    """Lee cookies de Edge (necesita Edge cerrado)."""
    cookie_file = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "Edge", "User Data", "Default", "Network", "Cookies"
    )
    if not os.path.exists(cookie_file):
        print(f"ERROR: No se encontro {cookie_file}")
        return None

    key = get_edge_encryption_key()
    tmp = os.path.join(tempfile.gettempdir(), "edge_cookies.db")
    shutil.copy2(cookie_file, tmp)

    try:
        conn = sqlite3.connect(tmp)
        cursor = conn.execute(
            "SELECT name, encrypted_value FROM cookies "
            "WHERE host_key LIKE '%.tiktok.com%'"
        )
        cookies = {}
        for name, enc_value in cursor.fetchall():
            try:
                cookies[name] = decrypt_cookie(enc_value, key)
            except Exception:
                pass
        conn.close()
        return cookies
    finally:
        os.unlink(tmp)


def extract_session(cookies):
    session_id = (cookies.get("sessionid")
                  or cookies.get("sessionid_ss")
                  or cookies.get("sid_tt"))
    return {
        "sessionid": session_id or "",
        "tt_target_idc": cookies.get("tt-target-idc", ""),
        "sid_tt": cookies.get("sid_tt", ""),
        "sessionid_ss": cookies.get("sessionid_ss", ""),
    }


def verify_session(session):
    ok = bool(session.get("sessionid"))
    if ok:
        print(f"OK: sesion valida (sessionid: ***{session['sessionid'][-4:]})")
        print(f"    tt_target_idc: {session['tt_target_idc']}")
    else:
        print("SIN SESION: no se encontro sessionid valida")
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--print", action="store_true", help="Imprimir JSON en pantalla")
    p.add_argument("--verify", action="store_true", help="Verificar cookies.json existente")
    args = p.parse_args()

    if args.verify:
        if not os.path.exists("cookies.json"):
            print("No hay cookies.json para verificar")
            sys.exit(1)
        with open("cookies.json", "r", encoding="utf-8") as f:
            session = json.load(f)
        if not verify_session(session):
            sys.exit(1)
        return

    # Verificar si Edge esta abierto
    if is_edge_running():
        print("Edge esta abierto. Necesito cerrarlo para leer las cookies.")
        print()
        resp = input("Cierra Edge y presiona Enter (o 'q' para cancelar): ").strip()
        if resp.lower() == "q":
            print("Cancelado. Puedes exportar manualmente con F12 en Edge.")
            sys.exit(0)
        # Esperar a que cierre
        import time
        for _ in range(10):
            if not is_edge_running():
                break
            time.sleep(1)
        if is_edge_running():
            print("Edge sigue abierto. Intenta de nuevo despues de cerrarlo.")
            sys.exit(1)

    print("Leyendo cookies de Edge...")
    cookies = read_cookies_from_edge()

    if not cookies:
        print("No se pudieron leer las cookies")
        sys.exit(1)

    session = extract_session(cookies)

    if not session.get("sessionid"):
        print("SIN SESION: Edge no tiene una sesion de TikTok activa.")
        print("Abre Edge, inicia sesion en tiktok.com, y vuelve a ejecutar.")
        sys.exit(1)

    print("OK: sesion encontrada en Edge")
    print(f"  sessionid: ***{session['sessionid'][-4:]}")
    print(f"  tt_target_idc: {session['tt_target_idc']}")

    if getattr(args, "print"):
        print(json.dumps(session, indent=2))
        return

    with open("cookies.json", "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    print()
    print("Guardado en cookies.json")
    print("Siguiente paso: copia el contenido a GitHub Secrets → TIKTOK_COOKIES")


if __name__ == "__main__":
    main()