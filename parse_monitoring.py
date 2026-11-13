#!/usr/bin/env python3
"""
Outil Python en ligne de commande pour parser, filtrer et extraire
les tableaux de statuts de flux depuis le texte d'un e-mail ou fichier.

Supporte :
- Multi-statuts : ex --status NOK,EN_COURS
- Filtre en minutes sur Time Since Last Execution : ex --min-minutes 60
"""

import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys
import webbrowser


def parse_duration_to_minutes(duration_str: str) -> float:
    """
    Convertit une chaîne Time Since Last Execution (ex: 3d:7h:10m:40s)
    en nombre total équivalent de minutes.
    """
    if not duration_str or not isinstance(duration_str, str):
        return None
    s = duration_str.strip().lower()

    days, hours, mins, secs = 0, 0, 0, 0

    d_match = re.search(r"(\d+)\s*d", s)
    h_match = re.search(r"(\d+)\s*h", s)
    m_match = re.search(r"(\d+)\s*m", s)
    s_match = re.search(r"(\d+)\s*s", s)

    matched = False
    if d_match:
        days = int(d_match.group(1))
        matched = True
    if h_match:
        hours = int(h_match.group(1))
        matched = True
    if m_match:
        mins = int(m_match.group(1))
        matched = True
    if s_match:
        secs = int(s_match.group(1))
        matched = True

    if not matched and ":" in s:
        parts = [float(p) for p in s.split(":") if p.strip().replace(".", "", 1).isdigit()]
        if len(parts) == 4:
            days, hours, mins, secs = parts
        elif len(parts) == 3:
            hours, mins, secs = parts
        elif len(parts) == 2:
            mins, secs = parts

    total = (days * 1440) + (hours * 60) + mins + round(secs / 60)
    return total


def normalize_status(raw: str) -> str:
    """Normalise un statut en OK, NOK, EN_COURS ou AUTRE."""
    if not raw:
        return "AUTRE"
    clean = raw.strip().upper()
    if any(k in clean for k in ["NOK", "KO", "ERREUR", "ERROR", "STOPPED", "FAILED"]):
        return "NOK"
    if any(k in clean for k in ["EN COURS", "PROGRESS", "RUNNING", "PENDING"]):
        return "EN_COURS"
    if any(k in clean for k in ["OK", "SUCCESS", "SUCCÈS", "DONE"]):
        return "OK"
    return "AUTRE"


def parse_email_text(raw_text: str) -> dict:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    header_idx = -1
    delimiter = None

    for i, line in enumerate(lines):
        if re.search(r"flux|flow|nom|process", line, re.I) or re.search(r"stat|etat", line, re.I):
            header_idx = i
            if "\t" in line:
                delimiter = "\t"
            elif "|" in line:
                delimiter = "|"
            else:
                delimiter = r"\s{2,}"
            break

    if header_idx == -1:
        for i, line in enumerate(lines):
            if re.search(r"\b(OK|NOK|En cours)\b", line, re.I):
                header_idx = i - 1 if i > 0 else 0
                delimiter = "\t" if "\t" in line else ("|" if "|" in line else r"\s{2,}")
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

        if all(re.match(r"^[-:]+$", p) for p in parts if p):
            continue

        if len(parts) >= 2:
            while len(parts) < len(columns):
                parts.append("")
            rows.append(parts)

    return {"columns": columns, "rows": rows}


def display_terminal_table(columns: list, rows: list, filter_statuses: list = None, min_minutes: float = None) -> None:
    status_idx = next((i for i, c in enumerate(columns) if re.search(r"stat", c, re.I)), -1)
    time_idx = next((i for i, c in enumerate(columns) if re.search(r"time since|since|durée", c, re.I)), -1)

    filtered = []
    for r in rows:
        if filter_statuses and status_idx != -1:
            norm = normalize_status(r[status_idx])
            if norm not in filter_statuses:
                continue

        if min_minutes is not None and time_idx != -1:
            dur = parse_duration_to_minutes(r[time_idx])
            if dur is None or dur < min_minutes:
                continue

        filtered.append(r)

    print("\n" + "═" * 84)
    print(f"📊 RÉSULTATS DE L'EXTRACTION ({len(filtered)} flux filtrés sur {len(rows)})")
    if filter_statuses:
        print(f"🎯 Filtre statut : {', '.join(filter_statuses)}")
    if min_minutes is not None:
        print(f"⏱️  Filtre durée  : >= {min_minutes} minutes")
    print("═" * 84)

    for r in filtered:
        flux = r[0] if len(r) > 0 else "-"
        stat = r[status_idx] if status_idx != -1 and len(r) > status_idx else "-"
        norm = normalize_status(stat)

        icon = "✅" if norm == "OK" else ("❌" if norm == "NOK" else ("⏳" if norm == "EN_COURS" else "ℹ️"))
        time_info = ""
        if time_idx != -1 and len(r) > time_idx:
            mins = parse_duration_to_minutes(r[time_idx])
            time_info = f" ({mins} min)"

        err = ""
        for i, col in enumerate(columns):
            if re.search(r"erreur|error", col, re.I) and i < len(r) and r[i]:
                err = f" -> ⚠️  {r[i]}"

        print(f" {icon} [{stat:^10}]  {flux:<38}{time_info}{err}")
    print("═" * 84 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Analyseur de statuts de flux avec calcul de minutes.")
    parser.add_argument("--file", "-f", help="Chemin vers un fichier texte.")
    parser.add_argument("--status", "-s", help="Statut(s) séparés par virgule (ex: NOK ou NOK,EN_COURS).")
    parser.add_argument("--min-minutes", "-m", type=float, help="Filtrer les flux dont Time Since >= N minutes.")
    parser.add_argument("--csv", "-c", help="Exporter les résultats filtrés en CSV.")
    parser.add_argument("--serve", action="store_true", help="Ouvrir le dashboard interactif dans le navigateur.")

    args = parser.parse_args()

    html_path = Path(__file__).resolve().parent / "dashboard.html"
    if args.serve or (len(sys.argv) == 1 and not sys.stdin.isatty()):
        pass
    elif len(sys.argv) == 1:
        if html_path.exists():
            print(f"🌐 Ouverture du Dashboard dans votre navigateur...")
            webbrowser.open(f"file://{html_path}")
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

    filter_statuses = None
    if args.status:
        filter_statuses = [normalize_status(s) for s in args.status.split(",") if s.strip()]

    display_terminal_table(data["columns"], data["rows"], filter_statuses, args.min_minutes)


if __name__ == "__main__":
    main()
