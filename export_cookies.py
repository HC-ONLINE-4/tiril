#!/usr/bin/env python3
"""
Exporta cookies de TikTok desde Edge.

METODO RAPIDO (30 segundos):

  1. Abre Edge, ve a https://www.tiktok.com (inicia sesion)
  2. Presiona F12 → pestaña "Application" (arriba)
  3. En el panel izquierdo: Storage → Cookies → https://www.tiktok.com
  4. Busca y doble-clic para copiar el VALOR de cada cookie:
     - sessionid
     - tt-target-idc
     - sid_tt
     - sessionid_ss
  5. Ejecuta: python export_cookies.py
  6. Pega los valores cuando pregunte

  O crea cookies.json manualmente con este formato:
  {
    "sessionid": "AQUI_EL_VALOR",
    "tt_target_idc": "AQUI_EL_VALOR",
    "sid_tt": "AQUI_EL_VALOR",
    "sessionid_ss": "AQUI_EL_VALOR"
  }
"""
import json
import sys


def main():
    print("=== Exportar cookies de TikTok ===")
    print()
    print("Necesito estos valores de Edge DevTools:")
    print("  F12 → Application → Cookies → tiktok.com")
    print()

    session = {}
    for field in ["sessionid", "tt_target_idc", "sid_tt", "sessionid_ss"]:
        val = input(f"  {field}: ").strip()
        session[field] = val

    if not session.get("sessionid"):
        print("\nERROR: sessionid esta vacio. No hay sesion de TikTok.")
        sys.exit(1)

    print(f"\nOK: sessionid: ***{session['sessionid'][-4:]}")

    with open("cookies.json", "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)

    print("Guardado en cookies.json")
    print()
    print("Siguiente paso:")
    print("  GitHub → tu repo → Settings → Secrets → Actions")
    print("  → New secret → Name: TIKTOK_COOKIES → Value: pega el JSON")


if __name__ == "__main__":
    main()