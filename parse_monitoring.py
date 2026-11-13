#!/usr/bin/env python3
"""
Outil Python en ligne de commande pour parser, filtrer et extraire
les tableaux de statuts de flux depuis le texte brut d'un e-mail.

Utilisation :
  python3 parse_monitoring.py                      # Ouvre le dashboard web dans votre navigateur
  python3 parse_monitoring.py --file email.txt     # Parse un fichier texte
  python3 parse_monitoring.py --status NOK         # Affiche uniquement les flux en erreur
  python3 parse_monitoring.py --csv export.csv     # Exporte en CSV
"""

import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys
import webbrowser


def normalize_status(raw: str) -> str:
    """Normalise un statut en OK, NOK, EN_COURS ou AUTRE."""
    if not raw:
        return "AUTRE"
    clean = raw.strip().upper()
    if any(k in clean for k in ["NOK", "KO", "ERREUR", "ERROR", "STOPPED"]):
        return "NOK"
    if any(k in clean for k in ["EN COURS", "PROGRESS", "RUNNING"]):
        return "EN_COURS"
    if any(k in clean for k in ["OK", "SUCCESS", "SUCCÈS"]):
        return "OK"
    return "AUTRE"


def parse_email_text(raw_text: str) -> dict:
    """Extrait automatiquement les colonnes et lignes du tableau d'un e-mail."""
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    header_idx = -1
    delimiter = None

    # 1. Détection de la ligne d'en-tête (cherche 'flux' et 'stat')
    for i, line in enumerate(lines):
        if re.search(r"flux", line, re.I) and re.search(r"stat", line, re.I):
            header_idx = i
            if "\t" in line:
                delimiter = "\t"
            elif "|" in line:
                delimiter = "|"
            else:
                delimiter = r"\s{2,}"
            break

    if header_idx == -1:
        return {"columns": [], "rows": []}

    header_line = lines[header_idx]
    if delimiter == "|":
        columns = [c.strip() for c in header_line.split("|") if c.strip()]
    elif delimiter == "\t":
        columns = [c.strip() for c in header_line.split("\t") if c.strip()]
    else:
        columns = [c.strip() for c in re.split(delimiter, header_line) if c.strip()]

    rows = []
    footer_pattern = re.compile(r"^(cordialement|this message|ce message|automonitoring|___)", re.I)

    # 2. Extraction des lignes de données
    for line in lines[header_idx + 1:]:
        if footer_pattern.search(line):
            break

        if delimiter == "|":
            parts = [p.strip() for p in line.split("|")]
            if line.startswith("|"):
                parts.pop(0)
            if line.endswith("|"):
                parts.pop()
        elif delimiter == "\t":
            parts = [p.strip() for p in line.split("\t")]
        else:
            parts = [p.strip() for p in re.split(delimiter, line)]

        # Ignorer les lignes de séparateur markdown (|---|---|)
        if all(re.match(r"^[-:]+$", p) for p in parts if p):
            continue

        if len(parts) >= 2:
            while len(parts) < len(columns):
                parts.append("")
            rows.append(parts)

    return {"columns": columns, "rows": rows}


def display_terminal_table(columns: list, rows: list, filter_status: str = None) -> None:
    """Affiche le tableau formaté dans le terminal avec coloration."""
    status_idx = next((i for i, c in enumerate(columns) if re.search(r"stat", c, re.I)), -1)

    filtered = rows
    if filter_status and status_idx != -1:
        filtered = [r for r in rows if normalize_status(r[status_idx]) == filter_status.upper()]

    print("\n" + "═" * 80)
    print(f"📊 RÉSULTATS DE L'EXTRACTION ({len(filtered)} flux affichés sur {len(rows)})")
    print("═" * 80)

    # Statistiques rapides
    ok_count = sum(1 for r in rows if normalize_status(r[status_idx]) == "OK")
    nok_count = sum(1 for r in rows if normalize_status(r[status_idx]) == "NOK")
    prog_count = sum(1 for r in rows if normalize_status(r[status_idx]) == "EN_COURS")
    print(f"✅ OK : {ok_count}  |  ❌ NOK : {nok_count}  |  ⏳ En cours : {prog_count}")
    print("─" * 80)

    for r in filtered:
        flux = r[0] if len(r) > 0 else "-"
        stat = r[status_idx] if status_idx != -1 and len(r) > status_idx else "-"
        norm = normalize_status(stat)

        # Icône selon statut
        icon = "✅" if norm == "OK" else ("❌" if norm == "NOK" else ("⏳" if norm == "EN_COURS" else "ℹ️"))
        err = ""
        # Chercher colonne d'erreur
        for i, col in enumerate(columns):
            if re.search(r"erreur|error", col, re.I) and i < len(r) and r[i]:
                err = f" -> ⚠️  {r[i]}"

        print(f" {icon} [{stat:^10}]  {flux:<40}{err}")
    print("═" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Analyseur de statuts de flux de monitoring.")
    parser.add_argument("--file", "-f", help="Chemin vers un fichier texte contenant l'e-mail.")
    parser.add_argument("--status", "-s", choices=["OK", "NOK", "EN_COURS"], help="Filtrer par statut.")
    parser.add_argument("--csv", "-c", help="Exporter les résultats dans un fichier CSV.")
    parser.add_argument("--serve", action="store_true", help="Ouvrir le dashboard interactif dans le navigateur.")

    args = parser.parse_args()

    # Si aucun argument ou --serve, ouvrir le dashboard web dans le navigateur
    html_path = Path(__file__).resolve().parent / "dashboard.html"
    if args.serve or (len(sys.argv) == 1 and not sys.stdin.isatty()):
        pass
    elif len(sys.argv) == 1:
        if html_path.exists():
            print(f"🌐 Ouverture du Dashboard interactif dans votre navigateur...")
            webbrowser.open(f"file://{html_path}")
            print(f"👉 Fichier : {html_path}")
            return

    content = ""
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            content = f.read()
    elif not sys.stdin.isatty():
        content = sys.stdin.read()
    else:
        print("💡 Astuce : Passez un fichier avec --file email.txt ou collez votre texte.")
        return

    data = parse_email_text(content)
    if not data["rows"]:
        print("❌ Aucun tableau de flux trouvé dans le contenu fourni.")
        sys.exit(1)

    display_terminal_table(data["columns"], data["rows"], args.status)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(data["columns"])
            writer.writerows(data["rows"])
        print(f"💾 Données exportées avec succès dans {args.csv}")


if __name__ == "__main__":
    main()
