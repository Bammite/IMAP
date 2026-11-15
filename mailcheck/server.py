"""Local web server for the Mailcheck Outlook collector."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict

from mailcheck.folder_config import add_folder, delete_folder, get_folders, update_folder
from mailcheck.mail_service import MailcheckError, collect_folder


APP_DIRECTORY = Path(__file__).resolve().parent
PROJECT_DIRECTORY = APP_DIRECTORY.parent
MAX_REQUEST_SIZE = 16_384
DEFAULT_PORT = 8000
STATIC_PAGES = {
    "/": PROJECT_DIRECTORY / "index.html",
    "/index.html": PROJECT_DIRECTORY / "index.html",
    "/dashboard.html": PROJECT_DIRECTORY / "dashboard.html",
    "/folders.html": APP_DIRECTORY / "folders.html",
    "/help.html": APP_DIRECTORY / "help.html",
    "/mailcheck": APP_DIRECTORY / "index.html",
    "/mailcheck/": APP_DIRECTORY / "index.html",
}


class MailcheckHandler(BaseHTTPRequestHandler):
    server_version = "Mailcheck/1.0"

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/folders":
            try:
                self._send_json(200, {"folders": get_folders()})
            except MailcheckError as exc:
                self._send_json(500, {"error": str(exc)})
            return

        if self.path == "/api/monitoring-raid":
            try:
                raid_folder = next(
                    (item for item in get_folders() if item["id"] == "monitoring-raid"),
                    None,
                )
                if raid_folder is None:
                    raise MailcheckError(
                        "Le dossier de supervision RAID n'est pas configuré dans la gestion des dossiers."
                    )
                if not raid_folder["enabled"]:
                    raise MailcheckError(
                        "Le dossier de supervision RAID est désactivé dans la gestion des dossiers."
                    )
                result = collect_folder(
                    raid_folder["outlook_folder"],
                    since_hours=4,
                    include_html=True,
                )
                result["window_hours"] = 4
                self._send_json(200, result)
            except MailcheckError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception:
                logging.exception("Erreur inattendue pendant l'analyse RAID Outlook.")
                self._send_json(500, {"error": "Erreur inattendue pendant l'analyse RAID. Consultez la console."})
            return

        page_path = STATIC_PAGES.get(self.path)
        if page_path is None:
            self.send_error(404, "Ressource introuvable")
            return

        try:
            body = page_path.read_bytes()
        except OSError:
            logging.exception("Impossible de lire la page Mailcheck.")
            self.send_error(500, "Page Mailcheck indisponible")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_request(self) -> Dict[str, Any]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Taille de requête invalide.")

        if content_length <= 0 or content_length > MAX_REQUEST_SIZE:
            raise ValueError("Requête vide ou trop volumineuse.")

        try:
            request_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Corps JSON invalide.")

        if not isinstance(request_data, dict):
            raise ValueError("Le corps doit être un objet JSON.")
        return request_data

    def do_POST(self) -> None:
        try:
            request_data = self._read_json_request()
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        try:
            if self.path == "/api/folders":
                folders = add_folder(
                    request_data.get("outlook_folder"),
                    request_data.get("alert_type"),
                    request_data.get("description", ""),
                )
                self._send_json(201, {"folders": folders})
                return

            if self.path == "/api/folders/update":
                folders = update_folder(
                    request_data.get("id"),
                    request_data.get("outlook_folder"),
                    request_data.get("alert_type"),
                    request_data.get("description", ""),
                    request_data.get("enabled", True),
                )
                self._send_json(200, {"folders": folders})
                return

            if self.path == "/api/folders/delete":
                folders = delete_folder(request_data.get("id"))
                self._send_json(200, {"folders": folders})
                return

            if self.path == "/api/check":
                folder_path = request_data.get("folder")
                if not isinstance(folder_path, str):
                    raise MailcheckError("Le champ « folder » est obligatoire.")
                result = collect_folder(folder_path)
                self._send_json(200, result)
                return

            self._send_json(404, {"error": "Route API introuvable."})
        except MailcheckError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception:
            logging.exception("Erreur inattendue pendant une requête Mailcheck.")
            self._send_json(500, {"error": "Erreur inattendue. Consultez la console du serveur."})

    def log_message(self, format_string: str, *args: Any) -> None:
        logging.info("%s - %s", self.address_string(), format_string % args)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        port = int(os.environ.get("MAILCHECK_PORT", str(DEFAULT_PORT)))
        if not 1 <= port <= 65535:
            raise ValueError("Le port doit être compris entre 1 et 65535.")
    except ValueError as exc:
        logging.error("Port invalide : %s", exc)
        return 2

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), MailcheckHandler)
    except OSError as exc:
        logging.error(
            "Impossible d'ouvrir http://localhost:%d/ (%s). "
            "Arrêtez l'ancien serveur du port 8000 avec Ctrl+C, puis relancez Mailcheck.",
            port,
            exc,
        )
        return 1

    logging.info("Mailcheck est disponible sur http://localhost:%d/", port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Arrêt de Mailcheck.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
