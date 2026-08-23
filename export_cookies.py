#!/usr/bin/env python3
"""
Exporta cookies de TikTok desde Microsoft Edge.

METODO 1 (automatico): python export_cookies.py
METODO 2 (manual - si el automatico falla):

  1. Abre Edge, ve a https://www.tiktok.com (inicia sesion si no lo estas)
  2. Presiona F12 → Application → Cookies → tiktok.com
  3. Busca estas cookies y copia sus valores:
     - sessionid
     - tt-target-idc
     - sid_tt
     - sessionid_ss
  4. Crea un archivo cookies.json con este formato:
     {
       "sessionid": "PEGA_AQUI_EL_VALOR",
       "tt_target_idc": "PEGA_AQUI_EL_VALOR",
       "sid_tt": "",
       "sessionid_ss": ""
     }
  5. En GitHub → Settings → Secrets → Actions → New repository secret
     Name: TIKTOK_COOKIES
     Value: pega todo el JSON anterior

Uso:
  python export_cookies.py              # intenta automatico
  python export_cookies.py --print      # imprime JSON para copiar
"""
import argparse
import json
import os
import sys


def try_automatic():
    """Intenta exportar automaticamente (solo funciona si Edge esta cerrado)."""
    import base64
    import shutil
    import sqlite3
    import tempfile

    try:
        import win32crypt
    except ImportError:
        return None

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return None

    local_state = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "Edge", "User Data", "Local State"
    )
    cookie_file = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "Edge", "User Data", "Default", "Network", "Cookies"
    )

    if not os.path.exists(local_state) or not os.path.exists(cookie_file):
        return None

    with open(local_state, "r", encoding="utf-8") as f:
        state = json.load(f)

    key = base64.b64decode(state["os_crypt"]["encrypted_key"])[5:]
    key = win32crypt.CryptUnprotectData(key, None, None, None, 0)[1]

    tmp = os.path.join(tempfile.gettempdir(), "edge_cookies.db")
    try:
        shutil.copy2(cookie_file, tmp)
    except PermissionError:
        return None  # Edge esta abierto

    try:
        conn = sqlite3.connect(tmp)
        cursor = conn.execute(
            "SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%.tiktok.com%'"
        )
        cookies = {}
        for name, enc_value in cursor.fetchall():
            try:
                if enc_value[:3] in (b"v10", b"v20"):
                    iv = enc_value[3:15]
                    tag = enc_value[-16:]
                    aes = AESGCM(key)
                    cookies[name] = aes.decrypt(iv, enc_value[15:-16], None).decode()
                else:
                    cookies[name] = win32crypt.CryptUnprotectData(enc_value, None, None, None, 0)[1].decode()
            except Exception:
                pass
        conn.close()
        return cookies
    finally:
        os.unlink(tmp)


def extract_session(cookies):
    session_id = cookies.get("sessionid") or cookies.get("sessionid_ss") or cookies.get("sid_tt")
    tt_target_idc = cookies.get("tt-target-idc", "")
    return {
        "sessionid": session_id or "",
        "tt_target_idc": tt_target_idc,
        "sid_tt": cookies.get("sid_tt", ""),
        "sessionid_ss": cookies.get("sessionid_ss", ""),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--print", action="store_true", help="Imprimir JSON en pantalla")
    args = p.parse_args()

    cookies = try_automatic()

    if cookies:
        session = extract_session(cookies)
        ok = bool(session.get("sessionid"))
        print(f"{'OK: sesion encontrada' if ok else 'SIN SESION: Edge no tiene sesion de TikTok activa'}")
        if ok:
            print(f"  sessionid: ***{session['sessionid'][-4:]}")
            print(f"  tt_target_idc: {session['tt_target_idc']}")
    else:
        print("No se pudo exportar automaticamente (Edge esta abierto).")
        print("Usa el METODO MANUAL descrito en el encabezado del script.")
        print()
        print("Resumen rapido:")
        print("  1. Abre Edge → tiktok.com (inicia sesion)")
        print("  2. F12 → Application → Cookies → tiktok.com")
        print("  3. Copia: sessionid, tt-target-idc, sid_tt, sessionid_ss")
        print('  4. Crea cookies.json: {"sessionid":"...","tt_target_idc":"...","sid_tt":"","sessionid_ss":""}')
        print("  5. En GitHub → Secrets → Actions → TIKTOK_COOKIES → pega el JSON")
        return

    if getattr(args, "print"):
        print(json.dumps(session, indent=2))
        return

    with open("cookies.json", "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)
    print("Guardado en cookies.json")


if __name__ == "__main__":
    main()