#!/usr/bin/env python3
"""
Script de test technique pour récupérer et afficher le dernier e-mail
reçu via le protocole IMAP sécurisé (IMAPS).

Contraintes respectées :
- Utilisation de la bibliothèque native imaplib et email de Python 3.
- Connexion IMAPS avec vérification TLS (ssl.create_default_context).
- Lecture seule stricte : INBOX en mode readonly et commande BODY.PEEK[]
  (garantit qu'aucun e-mail n'est supprimé, modifié ou marqué comme lu).
- Gestion sécurisée des identifiants via variables d'environnement / .env.
- Aucun mot de passe affiché en clair ni dans les traces d'erreur.
"""

import email
import email.message
from email.header import decode_header, make_header
import html.parser
import imaplib
import os
from pathlib import Path
import re
import socket
import ssl
import sys


def load_env_file(dotenv_path: Path = None) -> None:
    """
    Charge les variables d'un fichier .env dans os.environ.
    Fonctionne nativement sans dépendance externe obligatoire,
    tout en restant compatible avec python-dotenv si installé.
    """
    # 1. Tenter d'utiliser python-dotenv si présent
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path)
        return
    except ImportError:
        pass

    # 2. Repli autonome : lecture native du fichier .env
    target_path = dotenv_path or (Path(__file__).resolve().parent / ".env")
    if not target_path.exists():
        return

    with open(target_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            # Ignorer les commentaires et lignes vides
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key, val = stripped.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Supprimer les guillemets englobants si présents
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                # Ne pas écraser les variables déjà définies dans l'environnement
                if key not in os.environ:
                    os.environ[key] = val


class HTMLTextExtractor(html.parser.HTMLParser):
    """Convertit simplement du code HTML en texte brut si text/plain est absent."""
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_text(self) -> str:
        return "".join(self.fed).strip()


def strip_html(html_content: str) -> str:
    """Extrait le texte brut d'un contenu HTML."""
    try:
        parser = HTMLTextExtractor()
        parser.feed(html_content)
        return parser.get_text()
    except Exception:
        # Repli par expression régulière simple si le parseur échoue
        clean = re.sub(r"<[^>]+>", " ", html_content)
        return " ".join(clean.split())


def decode_mime_header(raw_header: str) -> str:
    """Décode un en-tête d'e-mail encodé en MIME (ex: UTF-8, ISO-8859-1)."""
    if not raw_header:
        return "(Non renseigné)"
    try:
        return str(make_header(decode_header(raw_header)))
    except Exception:
        return str(raw_header)


def extract_body_text(msg: email.message.Message) -> str:
    """
    Extrait le corps du message en texte brut prioritairement.
    Si seul le format HTML est disponible, en extrait le texte visible.
    """
    plain_text = None
    html_text = None

    if msg.is_multipart():
        for part in msg.walk():
            # Ignorer les pièces jointes
            content_disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in content_disposition.lower():
                continue

            content_type = part.get_content_type()
            charset = part.get_content_charset() or "utf-8"

            if content_type == "text/plain" and plain_text is None:
                payload = part.get_payload(decode=True)
                if payload:
                    plain_text = payload.decode(charset, errors="replace")
            elif content_type == "text/html" and html_text is None:
                payload = part.get_payload(decode=True)
                if payload:
                    html_text = payload.decode(charset, errors="replace")
    else:
        content_type = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode(charset, errors="replace")
            if content_type == "text/plain":
                plain_text = text
            elif content_type == "text/html":
                html_text = text

    if plain_text and plain_text.strip():
        return plain_text.strip()
    elif html_text and html_text.strip():
        return f"[Format HTML converti en texte brut]\n\n{strip_html(html_text)}"
    return "(Aucun contenu textuel disponible dans ce message)"


def main() -> None:
    # 1. Charger les variables d'environnement
    load_env_file()

    imap_server = os.getenv("IMAP_SERVER")
    imap_port_raw = os.getenv("IMAP_PORT", "993")
    imap_user = os.getenv("IMAP_USER")
    imap_password = os.getenv("IMAP_PASSWORD")

    # 2. Validation des variables requises
    missing_vars = []
    if not imap_server:
        missing_vars.append("IMAP_SERVER")
    if not imap_user:
        missing_vars.append("IMAP_USER")
    if not imap_password:
        missing_vars.append("IMAP_PASSWORD")

    if missing_vars:
        print("❌ Erreur de configuration : Variables d'environnement manquantes :", file=sys.stderr)
        for var in missing_vars:
            print(f"   - {var}", file=sys.stderr)
        print("\nAssurez-vous d'avoir complété votre fichier .env avec ces paramètres.", file=sys.stderr)
        sys.exit(1)

    try:
        imap_port = int(imap_port_raw)
    except ValueError:
        print(f"❌ Erreur : Port IMAP invalide ('{imap_port_raw}'). 993 attendu par défaut.", file=sys.stderr)
        sys.exit(1)

    print("════════════════════════════════════════════════════════════════")
    print("   TEST TECHNIQUE : LECTURE SÉCURISÉE DU DERNIER E-MAIL (IMAP)  ")
    print("════════════════════════════════════════════════════════════════")
    print(f"🔌 Serveur IMAP : {imap_server}:{imap_port}")
    print(f"👤 Utilisateur  : {imap_user}")
    print("🔒 Sécurité     : IMAPS avec validation TLS stricte")
    print("────────────────────────────────────────────────────────────────")

    # 3. Création d'un contexte TLS sécurisé (validation stricte des certificats)
    ssl_context = ssl.create_default_context()

    client = None
    try:
        # 4. Connexion au serveur IMAPS
        print("⏳ Connexion au serveur IMAP...")
        client = imaplib.IMAP4_SSL(host=imap_server, port=imap_port, ssl_context=ssl_context)
        print("✅ Connexion réseau et négociation TLS réussies.")

        # 5. Authentification
        print(f"⏳ Authentification de l'utilisateur '{imap_user}'...")
        client.login(imap_user, imap_password)
        print("✅ Authentification validée avec succès.")

        # 6. Accès au dossier INBOX en lecture seule (readonly=True)
        # Empêche toute modification de drapeau ou altération sur le serveur
        print("⏳ Accès au dossier INBOX en mode lecture seule...")
        status, folder_data = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError(f"Impossible d'accéder au dossier INBOX (statut: {status})")

        print("✅ Dossier INBOX ouvert en lecture seule.")

        # 7. Recherche de tous les identifiants de messages
        status, search_data = client.search(None, "ALL")
        if status != "OK" or not search_data or not search_data[0]:
            print("\n📭 La boîte de réception INBOX est vide. Aucun e-mail à afficher.")
            return

        message_ids = search_data[0].split()
        total_emails = len(message_ids)
        print(f"📊 Nombre total d'e-mails trouvés dans INBOX : {total_emails}")

        # Sélection du dernier e-mail reçu (le dernier ID de la séquence)
        last_id = message_ids[-1]
        print(f"⏳ Récupération du dernier e-mail (ID: {last_id.decode('ascii', errors='ignore')}) sans altération...")

        # 8. Récupération via BODY.PEEK[] pour préserver l'état 'non lu' s'il l'était
        status, msg_data = client.fetch(last_id, "(BODY.PEEK[])")
        if status != "OK" or not msg_data:
            raise RuntimeError(f"Échec de la récupération du message ID {last_id}")

        # Extraire le contenu brut du message RFC822
        raw_email = None
        for response_part in msg_data:
            if isinstance(response_part, tuple) and len(response_part) >= 2:
                raw_email = response_part[1]
                break

        if not raw_email:
            raise RuntimeError("Données de l'e-mail introuvables dans la réponse du serveur.")

        # 9. Parsing du message avec le module email natif
        parsed_email = email.message_from_bytes(raw_email)

        # Extraction des en-têtes demandés
        expediteur = decode_mime_header(parsed_email.get("From"))
        destinataire = decode_mime_header(parsed_email.get("To"))
        objet = decode_mime_header(parsed_email.get("Subject"))
        date_reception = decode_mime_header(parsed_email.get("Date"))

        # Extraction du corps de texte
        corps_texte = extract_body_text(parsed_email)

        # 10. Affichage des résultats dans le terminal
        print("\n" + "=" * 64)
        print("   📬 DERNIER E-MAIL REÇU (RÉSULTATS DE L'EXTRACTION)   ")
        print("=" * 64)
        print(f"📤 Expéditeur   : {expediteur}")
        print(f"📥 Destinataire : {destinataire}")
        print(f"📅 Date         : {date_reception}")
        print(f"🏷️  Objet        : {objet}")
        print("-" * 64)
        print("📝 Contenu du message (texte) :")
        print("-" * 64)
        print(corps_texte)
        print("=" * 64)
        print("\n✨ Test technique terminé avec succès (lecture seule, état préservé).")

    except imaplib.IMAP4.error as e:
        print("\n❌ Erreur IMAP :", file=sys.stderr)
        print(f"   Détail : {e}", file=sys.stderr)
        print("   Vérifiez vos identifiants ou si un mot de passe d'application est requis.", file=sys.stderr)
        sys.exit(2)
    except ssl.SSLError as e:
        print("\n❌ Erreur SSL/TLS :", file=sys.stderr)
        print(f"   Détail : {e}", file=sys.stderr)
        print("   Le serveur IMAP a rejeté la négociation sécurisée ou le certificat est invalide.", file=sys.stderr)
        sys.exit(3)
    except socket.gaierror as e:
        print("\n❌ Erreur de résolution de nom de domaine (DNS) :", file=sys.stderr)
        print(f"   Détail : {e}", file=sys.stderr)
        print("   Vérifiez l'adresse de votre serveur IMAP.", file=sys.stderr)
        sys.exit(4)
    except Exception as e:
        print("\n❌ Erreur inattendue :", file=sys.stderr)
        print(f"   {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(5)
    finally:
        # Fermeture propre de la session
        if client:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass


if __name__ == "__main__":
    main()
