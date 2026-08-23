#!/usr/bin/env python3
"""
Exporta cookies de TikTok desde Microsoft Edge.

METODO: Copiar desde Edge DevTools (siempre funciona).

Pasos:
  1. Abre Edge, ve a https://www.tiktok.com
  2. Inicia sesion con tu cuenta si no lo estas
  3. Presiona F12 → pestaña "Application" (arriba)
  4. En el panel izquierdo: Storage → Cookies → https://www.tiktok.com
  5. Busca estas filas en la tabla de la derecha:
     - sessionid
     - tt-target-idc
     - sid_tt
     - sessionid_ss
  6. Copia el VALOR de cada una (doble clic sobre el valor)
  7. Ejecuta este script y pegalo cuando pregunte:
       python export_cookies.py

  O crea manualmente cookies.json con este formato:
     {
       "sessionid": "PEGA_EL_VALOR_AQUI",
       "tt_target_idc": "PEGA_EL_VALOR_AQUI",
       "sid_tt": "PEGA_EL_VALOR_AQUI",
       "sessionid_ss": "PEGA_EL_VALOR_AQUI"
     }
"""
import json
import sys


def main():
    print("=== Exportar cookies de TikTok desde Edge ===")
    print()
    print("Abre Edge → tiktok.com → F12 → Application → Cookies → tiktok.com")
    print("Copia los valores de estas cookies:")
    print("  - sessionid")
    print("  - tt-target-idc")
    print("  - sid_tt")
    print("  - sessionid_ss")
    print()

    session = {}
    for field in ["sessionid", "tt_target_idc", "sid_tt", "sessionid_ss"]:
        val = input(f"  {field}: ").strip()
        session[field] = val

    ok = bool(session.get("sessionid"))
    if not ok:
        print("\nERROR: sessionid esta vacio. No hay sesion de TikTok en Edge.")
        print("Inicia sesion en tiktok.com y vuelve a intentar.")
        sys.exit(1)

    print(f"\nOK: sesion encontrada (sessionid: ***{session['sessionid'][-4:]})")
    print(f"    tt_target_idc: {session['tt_target_idc']}")

    with open("cookies.json", "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)

    print("\nGuardado en cookies.json")
    print("Siguiente paso:")
    print("  1. Abre GitHub → tu repo → Settings → Secrets → Actions")
    print("  2. New repository secret")
    print("  3. Name: TIKTOK_COOKIES")
    print("  4. Value: copia TODO el contenido de cookies.json")


if __name__ == "__main__":
    main()