"""Persistence for Outlook folders configured in the supervision hub."""

from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid
from typing import Any, Dict, List

from mailcheck.mail_service import MailcheckError, validate_folder_path


CONFIG_PATH = Path(__file__).resolve().parent / "data" / "folders.json"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "data" / "folders.example.json"
CONFIG_FORMAT = "mailcheck-folder-config"
CONFIG_VERSION = 1
_CONFIG_LOCK = threading.RLock()


def _read_config() -> List[Dict[str, Any]]:
    source_path = CONFIG_PATH if CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH
    if not source_path.exists():
        return []

    try:
        config = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MailcheckError(f"Le fichier de configuration des dossiers est illisible : {exc}") from exc

    if (
        not isinstance(config, dict)
        or config.get("format") != CONFIG_FORMAT
        or config.get("version") != CONFIG_VERSION
        or not isinstance(config.get("folders"), list)
    ):
        raise MailcheckError(
            "Le format du fichier de configuration des dossiers est invalide. "
            "Le fichier n'a pas été modifié."
        )

    for folder in config["folders"]:
        if (
            not isinstance(folder, dict)
            or not isinstance(folder.get("id"), str)
            or not isinstance(folder.get("outlook_folder"), str)
            or not isinstance(folder.get("alert_type"), str)
            or not isinstance(folder.get("description", ""), str)
            or not isinstance(folder.get("enabled"), bool)
            or not isinstance(folder.get("created_at"), str)
        ):
            raise MailcheckError(
                "Une entrée du fichier de configuration est invalide. "
                "Le fichier n'a pas été modifié."
            )
    return config["folders"]


def _write_config(folders: List[Dict[str, Any]]) -> None:
    config = {
        "format": CONFIG_FORMAT,
        "version": CONFIG_VERSION,
        "folders": folders,
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_PATH.parent,
            prefix=".folders-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(config, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        os.replace(temporary_path, CONFIG_PATH)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise MailcheckError(f"Impossible d'enregistrer la configuration des dossiers : {exc}") from exc


def get_folders() -> List[Dict[str, Any]]:
    with _CONFIG_LOCK:
        return _read_config()


def add_folder(
    outlook_folder: str,
    alert_type: str,
    description: str = "",
) -> List[Dict[str, Any]]:
    normalized_path = validate_folder_path(outlook_folder)
    if not isinstance(alert_type, str) or not isinstance(description, str):
        raise MailcheckError("Le type d'alerte et la description doivent être du texte.")
    alert_type = alert_type.strip()
    description = description.strip()
    if not alert_type:
        raise MailcheckError("Indiquez un type d'alerte.")
    if len(alert_type) > 120 or len(description) > 500:
        raise MailcheckError("Le type d'alerte ou la description dépasse la longueur autorisée.")

    with _CONFIG_LOCK:
        folders = _read_config()
        if any(item["outlook_folder"].casefold() == normalized_path.casefold() for item in folders):
            raise MailcheckError(f"Le dossier « {normalized_path} » est déjà configuré.")
        folders.append({
            "id": uuid.uuid4().hex,
            "outlook_folder": normalized_path,
            "alert_type": alert_type,
            "description": description,
            "enabled": True,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        _write_config(folders)
        return folders


def update_folder(
    folder_id: str,
    outlook_folder: str,
    alert_type: str,
    description: str = "",
    enabled: bool = True,
) -> List[Dict[str, Any]]:
    normalized_path = validate_folder_path(outlook_folder)
    if not isinstance(alert_type, str) or not isinstance(description, str):
        raise MailcheckError("Le type d'alerte et la description doivent être du texte.")
    alert_type = alert_type.strip()
    description = description.strip()
    if not folder_id or not alert_type:
        raise MailcheckError("L'identifiant du dossier et son type d'alerte sont obligatoires.")
    if len(alert_type) > 120 or len(description) > 500:
        raise MailcheckError("Le type d'alerte ou la description dépasse la longueur autorisée.")
    if not isinstance(enabled, bool):
        raise MailcheckError("L'état actif/inactif doit être un booléen.")

    with _CONFIG_LOCK:
        folders = _read_config()
        target = next((item for item in folders if item["id"] == folder_id), None)
        if target is None:
            raise MailcheckError("Le dossier à modifier n'existe pas.")
        if any(
            item["id"] != folder_id
            and item["outlook_folder"].casefold() == normalized_path.casefold()
            for item in folders
        ):
            raise MailcheckError(f"Le dossier « {normalized_path} » est déjà configuré.")

        target.update({
            "outlook_folder": normalized_path,
            "alert_type": alert_type,
            "description": description,
            "enabled": enabled,
        })
        _write_config(folders)
        return folders


def delete_folder(folder_id: str) -> List[Dict[str, Any]]:
    if not folder_id:
        raise MailcheckError("L'identifiant du dossier est obligatoire.")
    if folder_id == "monitoring-raid":
        raise MailcheckError("Le dossier système de l'analyse RAID ne peut pas être supprimé.")
    with _CONFIG_LOCK:
        folders = _read_config()
        remaining = [item for item in folders if item["id"] != folder_id]
        if len(remaining) == len(folders):
            raise MailcheckError("Le dossier à supprimer n'existe pas.")
        _write_config(remaining)
        return remaining
