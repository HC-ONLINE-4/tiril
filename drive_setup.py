# Regenera el acceso a Google Drive como TU propia cuenta (OAuth) y, opcionalmente,
# actualiza el secret DRIVE_OAUTH_JSON de GitHub automaticamente.
#
# El token expira cada 7 dias mientras la app este en modo "Pruebas" en Google Cloud.
# Para que JAMAS expire: Google Cloud Console -> API y servicios ->
# Pantalla de consentimiento -> Publicar app.
#
# Uso:
#   python drive_setup.py client_secret.json
#       -> genera drive_oauth.json (pega su contenido en el secret DRIVE_OAUTH_JSON)
#   python drive_setup.py client_secret.json --pat ghp_xxx
#       -> ademas actualiza el secret DRIVE_OAUTH_JSON de GitHub solo
import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
SECRET_NAME = "DRIVE_OAUTH_JSON"
REPO = "HC-ONLINE-4/tiril"


def update_github_secret(pat: str, value: str) -> None:
    import nacl.bindings

    base = f"https://api.github.com/repos/{REPO}/actions/secrets"
    h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    req = urllib.request.Request(f"{base}/public-key", headers=h)
    with urllib.request.urlopen(req) as r:
        pub = json.loads(r.read())
    key = base64.b64decode(pub["key"])
    sealed = nacl.bindings.crypto_box_seal(value.encode("utf-8"), key)
    body = json.dumps({
        "encrypted_value": base64.b64encode(sealed).decode("ascii"),
        "key_id": pub["key_id"],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/{SECRET_NAME}", data=body, method="PUT",
        headers={**h, "Content-Type": "application/json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    with urllib.request.urlopen(req) as r:
        print("Secret DRIVE_OAUTH_JSON actualizado en GitHub!")


def main():
    p = argparse.ArgumentParser(description="Genera el token OAuth de Google Drive")
    p.add_argument("client_secret", help="El client_secret.json descargado de Google Cloud")
    p.add_argument("--pat", default="",
                   help="Fine-grained token de GitHub con Actions Secrets: read/write (opcional)")
    args = p.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")

    if not creds.refresh_token:
        print("ERROR: no se obtuvo refresh_token.")
        sys.exit(1)

    data = {
        "type": "authorized_user",
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }
    Path("drive_oauth.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("Listo: drive_oauth.json generado.")

    if args.pat:
        try:
            update_github_secret(args.pat, json.dumps(data))
        except Exception as e:
            print(f"AVISO: no se pudo actualizar el secret en GitHub: {e}")
            print("Pega el contenido de drive_oauth.json en el secret DRIVE_OAUTH_JSON manualmente.")
    else:
        print("(Sin --pat) Pega el contenido de drive_oauth.json en el secret DRIVE_OAUTH_JSON.")
    print("IMPORTANTE: autoriza con la MISMA cuenta de Google duena de la carpeta TikTokGrabaciones.")


if __name__ == "__main__":
    main()