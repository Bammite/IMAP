"""Read Outlook mail from a folder and save a local JSON snapshot."""

from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple


INBOX_FOLDER_TYPE = 6
MAIL_ITEM_CLASS = 43
PROJECT_ROOT = Path(__file__).resolve().parent.parent
COLLECTION_ROOT = Path(__file__).resolve().parent / "data" / "mailcollect"


class MailcheckError(Exception):
    """Expected error that can be shown to the local user."""


def _folder_parts(folder_path: str) -> List[str]:
    if not isinstance(folder_path, str) or not folder_path.strip():
        raise MailcheckError("Indiquez un dossier Outlook, par exemple PICO.")

    parts = [part.strip() for part in re.split(r"[\\/]", folder_path.strip())]
    if any(not part or part in {".", ".."} for part in parts):
        raise MailcheckError("Le chemin doit contenir des noms de dossiers valides.")
    return parts


def _find_child_folder(parent: Any, requested_name: str) -> Any:
    children = parent.Folders
    available = []
    for index in range(1, children.Count + 1):
        child = children.Item(index)
        name = str(child.Name)
        available.append(name)
        if name.casefold() == requested_name.casefold():
            return child

    parent_name = str(parent.Name)
    choices = ", ".join(available) if available else "(aucun sous-dossier)"
    raise MailcheckError(
        f"Le dossier « {requested_name} » est introuvable sous « {parent_name} ». "
        f"Sous-dossiers disponibles : {choices}"
    )


def _resolve_inbox_folder(inbox: Any, folder_path: str) -> Any:
    folder = inbox
    for part in _folder_parts(folder_path):
        folder = _find_child_folder(folder, part)
    return folder


def _received_datetime(value: Any) -> Tuple[datetime, str]:
    if isinstance(value, datetime):
        return value, value.strftime("%Y-%m-%d %H:%M:%S")
    if value is None:
        raise MailcheckError("Un e-mail ne fournit pas de date de réception.")
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise MailcheckError(f"Date de réception Outlook invalide : {text}") from exc
    return parsed, text


def _safe_collection_path(folder_path: str) -> Path:
    parts = _folder_parts(folder_path)
    safe_parts = []
    for part in parts:
        safe_part = re.sub(r"[^\w.-]", "_", part, flags=re.UNICODE).strip(" .")
        if not safe_part or safe_part in {".", ".."}:
            raise MailcheckError(f"Nom de dossier non utilisable pour le stockage : {part}")
        safe_parts.append(safe_part)
    return COLLECTION_ROOT.joinpath(*safe_parts)


def validate_folder_path(folder_path: str) -> str:
    return "/".join(_folder_parts(folder_path))


def collect_folder(
    folder_path: str,
    since_hours: Optional[int] = None,
    include_html: bool = False,
) -> Dict[str, Any]:
    """Read mail items from an Inbox subfolder and persist a JSON snapshot."""
    folder_path = validate_folder_path(folder_path)
    if since_hours is not None and (not isinstance(since_hours, int) or not 1 <= since_hours <= 168):
        raise MailcheckError("La période de collecte doit être comprise entre 1 et 168 heures.")
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise MailcheckError(
            "pywin32 n'est pas installé. Installez-le avec : py -m pip install pywin32"
        ) from exc

    pythoncom.CoInitialize()
    try:
        try:
            outlook = win32com.client.GetActiveObject("Outlook.Application")
        except Exception as exc:
            raise MailcheckError(
                "Impossible de se connecter à Outlook. Ouvrez Outlook sur ce PC "
                "et vérifiez qu'un profil est configuré."
            ) from exc

        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(INBOX_FOLDER_TYPE)
        folder = _resolve_inbox_folder(inbox, folder_path)
        items = folder.Items
        messages = []
        latest_sort_key = None
        latest_email = None
        cutoff = None
        if since_hours is not None:
            cutoff = datetime.now().astimezone().replace(tzinfo=None) - timedelta(hours=since_hours)

        # La lecture se fait en sens inverse, sans déplacer ni modifier les éléments.
        for index in range(items.Count, 0, -1):
            item = items.Item(index)
            if int(item.Class) != MAIL_ITEM_CLASS:
                continue

            received, received_text = _received_datetime(item.ReceivedTime)
            comparable_received = (
                received.astimezone().replace(tzinfo=None)
                if received.tzinfo is not None
                else received
            )
            if cutoff is not None and comparable_received < cutoff:
                continue

            email_data = {
                "subject": str(item.Subject or ""),
                "sender": str(item.SenderName or ""),
                "sender_email": str(item.SenderEmailAddress or ""),
                "received_at": received_text,
                "body": str(item.Body or ""),
            }
            if include_html:
                email_data["html_body"] = str(item.HTMLBody or "")
            messages.append(email_data)

            if latest_sort_key is None or comparable_received > latest_sort_key:
                latest_sort_key = comparable_received
                latest_email = email_data

        output_directory = _safe_collection_path(folder_path)
        output_directory.mkdir(parents=True, exist_ok=True)
        collected_at = datetime.now().astimezone()
        filename = f"collecte_{collected_at.strftime('%Y%m%d_%H%M%S_%f')}.json"
        output_file = output_directory / filename
        temporary_file = output_file.with_suffix(".json.tmp")

        snapshot = {
            "folder": str(folder.FolderPath),
            "collected_at": collected_at.isoformat(timespec="seconds"),
            "count": len(messages),
            "latest_email": latest_email,
            "emails": messages,
        }
        try:
            temporary_file.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_file.replace(output_file)
        except OSError as exc:
            raise MailcheckError(f"Impossible d'enregistrer la collecte : {exc}") from exc

        logging.info(
            "%d e-mail(s) collecté(s) depuis %s dans %s",
            len(messages),
            folder.FolderPath,
            output_file,
        )
        result = {
            "folder": str(folder.FolderPath),
            "count": len(messages),
            "latest_email": latest_email,
            "file": str(output_file.relative_to(PROJECT_ROOT)),
        }
        if include_html:
            result["emails"] = messages
        return result
    finally:
        pythoncom.CoUninitialize()
