# Configura el acceso a Google Drive como TU propia cuenta (OAuth).
# Se corre UNA VEZ en tu PC. Genera drive_oauth.json; luego pega el contenido
# de ese archivo en el secret DRIVE_OAUTH_JSON del repo (Settings > Secrets).
#
# Uso:  python drive_setup.py client_secret.json
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main():
    if len(sys.argv) != 2:
        print("Uso: python drive_setup.py client_secret.json")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
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
    with open("drive_oauth.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("Listo: se genero drive_oauth.json")
    print("Abrelo, copia TODO el contenido y pegalo en el secret DRIVE_OAUTH_JSON de GitHub.")
    print("IMPORTANTE: autoriza con la MISMA cuenta de Google que dueno de la carpeta TikTokGrabaciones.")


if __name__ == "__main__":
    main()
