#!/usr/bin/env python3
"""
Récupération sécurisée du dernier e-mail via l'API Microsoft Graph (Exchange Online).
Compatible avec Microsoft Authenticator (MFA) via le flux Device Code (OAuth2).

Contraintes respectées :
- Utilise uniquement la bibliothèque standard Python 3 (urllib, json, ssl, os).
- Aucune modification de l'e-mail (lecture seule, état non-lu préservé).
- Authentification interactive compatible avec Microsoft Authenticator.
- Aucun mot de passe manipulé ni stocké en clair.
"""

import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def load_env_file(dotenv_path: Path = None) -> None:
    """Charge les variables du fichier .env sans dépendance externe."""
    target_path = dotenv_path or (Path(__file__).resolve().parent / ".env")
    if not target_path.exists():
        return

    with open(target_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, val = stripped.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if key not in os.environ:
                    os.environ[key] = val


def request_json(url: str, data: dict = None, headers: dict = None, method: str = None) -> dict:
    """Effectue un appel HTTP sécurisé et retourne le JSON décodé."""
    req_headers = {"User-Agent": "Exchange-Graph-Email-Reader/1.0"}
    if headers:
        req_headers.update(headers)

    body_bytes = None
    if data is not None:
        body_bytes = urllib.parse.urlencode(data).encode("utf-8")
        req_headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        try:
            return {"_http_error": e.code, "error_details": json.loads(err_body)}
        except Exception:
            return {"_http_error": e.code, "error_details": err_body}


def authenticate_device_flow(client_id: str, tenant: str, scopes: list) -> str:
    """
    Démarre l'authentification interactive Device Code Flow.
    Permet à l'utilisateur de valider son compte avec Microsoft Authenticator.
    """
    device_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode"
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    scope_str = " ".join(scopes)
    device_payload = {
        "client_id": client_id,
        "scope": scope_str,
    }

    print("⏳ Initialisation de la session d'authentification Microsoft 365...")
    res = request_json(device_url, data=device_payload)

    if "_http_error" in res or "user_code" not in res:
        err_msg = res.get("error_details", res.get("error_description", "Erreur inconnue"))
        raise RuntimeError(f"Échec d'initialisation Device Code : {err_msg}")

    user_code = res["user_code"]
    verification_uri = res.get("verification_uri", "https://microsoft.com/devicelogin")
    device_code = res["device_code"]
    interval = int(res.get("interval", 5))
    expires_in = int(res.get("expires_in", 900))

    print("\n" + "╔" + "═" * 62 + "╗")
    print("║          🔐 VALIDATION MICROSOFT AUTHENTICATOR               ║")
    print("╠" + "═" * 62 + "╣")
    print(f"║ 1. Ouvrez dans votre navigateur :                            ║")
    print(f"║    👉  \033[1;34m{verification_uri}\033[0m")
    print("║                                                              ║")
    print(f"║ 2. Entrez le code suivant :                                  ║")
    print(f"║    👉  \033[1;32m{user_code}\033[0m")
    print("║                                                              ║")
    print("║ 3. Connectez-vous avec votre compte Sonatel et validez sur   ║")
    print("║    votre application Microsoft Authenticator sur mobile.    ║")
    print("╚" + "═" * 62 + "╝\n")
    print("⏳ En attente de votre validation sur l'application Authenticator...")

    start_time = time.time()
    token_payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": client_id,
        "device_code": device_code,
    }

    while time.time() - start_time < expires_in:
        time.sleep(interval)
        token_res = request_json(token_url, data=token_payload)

        if "access_token" in token_res:
            print("\n✅ Authentification validée avec succès via Authenticator !")
            return token_res["access_token"]

        error_code = ""
        if isinstance(token_res.get("error_details"), dict):
            error_code = token_res["error_details"].get("error", "")
        elif "error" in token_res:
            error_code = token_res["error"]

        if error_code == "authorization_pending":
            print(".", end="", flush=True)
            continue
        elif error_code == "slow_down":
            time.sleep(interval)
            continue
        elif error_code == "expired_token":
            raise TimeoutError("Le code d'authentification a expiré.")
        elif error_code:
            desc = token_res.get("error_details", {}).get("error_description", error_code)
            raise RuntimeError(f"Erreur d'authentification : {desc}")

    raise TimeoutError("Délai d'attente de validation dépassé.")


def get_latest_email(access_token: str) -> dict:
    """Récupère le dernier e-mail de la boîte de réception via Microsoft Graph API."""
    # $top=1 pour ne récupérer qu'un seul e-mail
    # Prefer: outlook.body-content-type="text" pour obtenir directement le texte brut
    url = (
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages"
        "?$top=1"
        "&$select=from,toRecipients,subject,receivedDateTime,body"
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Prefer": 'outlook.body-content-type="text"',
    }

    res = request_json(url, headers=headers)
    if "_http_error" in res:
        raise RuntimeError(f"Erreur Graph API : {res.get('error_details')}")

    messages = res.get("value", [])
    if not messages:
        return None
    return messages[0]


def main():
    load_env_file()

    # Identifiant client officiel Microsoft Office / Outlook (pré-autorisé nativement)
    client_id = os.getenv("AZURE_CLIENT_ID", "d3590433-4474-42e6-a0cc-eb76470d74fb")
    # Déterminer automatiquement le tenant à partir du domaine de l'e-mail ou du .env
    user_email = os.getenv("IMAP_USER", "")
    domain = user_email.split("@")[-1] if "@" in user_email else "orange-sonatel.com"
    tenant = os.getenv("AZURE_TENANT_ID", domain)

    scopes = [
        "https://graph.microsoft.com/Mail.Read",
        "offline_access",
        "openid",
    ]

    print("════════════════════════════════════════════════════════════════")
    print("   TEST TECHNIQUE : LECTURE DU DERNIER E-MAIL (MICROSOFT 365)  ")
    print("════════════════════════════════════════════════════════════════")
    print(f"🏢 Tenant      : {tenant}")
    print("🔒 Protocole   : Microsoft Graph API (HTTPS / TLS 1.3 - Port 443)")
    print("📱 Sécurité    : OAuth2 Device Flow + Microsoft Authenticator (MFA)")
    print("────────────────────────────────────────────────────────────────")

    try:
        # 1. Authentification interactive (MFA Authenticator)
        token = authenticate_device_flow(client_id, tenant, scopes)

        # 2. Récupération du dernier e-mail
        print("\n⏳ Récupération du dernier e-mail dans la boîte de réception (INBOX)...")
        latest = get_latest_email(token)

        if not latest:
            print("\n📭 Votre boîte de réception INBOX est vide. Aucun e-mail trouvé.")
            return

        # 3. Extraction des champs
        from_info = latest.get("from", {}).get("emailAddress", {})
        expediteur = f"{from_info.get('name', '')} <{from_info.get('address', '')}>".strip()

        recipients = latest.get("toRecipients", [])
        destinataires = ", ".join(
            [f"{r.get('emailAddress', {}).get('address', '')}" for r in recipients]
        ) or "(Aucun destinataire)"

        objet = latest.get("subject") or "(Sans objet)"
        date_reception = latest.get("receivedDateTime") or "(Date inconnue)"

        body_info = latest.get("body", {})
        contenu = body_info.get("content", "").strip() or "(Message vide)"

        # 4. Affichage des résultats
        print("\n" + "=" * 64)
        print("   📬 DERNIER E-MAIL REÇU (RÉSULTATS DE L'EXTRACTION)   ")
        print("=" * 64)
        print(f"📤 Expéditeur   : {expediteur}")
        print(f"📥 Destinataire : {destinataires}")
        print(f"📅 Date         : {date_reception}")
        print(f"🏷️  Objet        : {objet}")
        print("-" * 64)
        print("📝 Contenu du message (texte) :")
        print("-" * 64)
        print(contenu)
        print("=" * 64)
        print("\n✨ Test technique validé avec succès (Exchange Online via HTTPS/Graph).")

    except Exception as e:
        print(f"\n❌ Erreur : {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
