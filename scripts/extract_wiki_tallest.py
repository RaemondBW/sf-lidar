#!/usr/bin/env python3
"""Extract the top N tallest SF buildings with coordinates from Wikipedia."""

from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from typing import List, Dict, Optional

import requests


WIKI_URL = "https://en.wikipedia.org/wiki/List_of_tallest_buildings_in_San_Francisco"
COORD_RE = re.compile(r"(-?\d+\.\d+)\s*;\s*(-?\d+\.\d+)")


class WikiTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: List[List[str]] = []
        self._current_row: List[str] = []
        self._current_cell: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table":
            self._in_table = True
            self._current_table = []
        if not self._in_table:
            return
        if tag == "tr":
            self._in_row = True
            self._current_row = []
        if tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._in_cell:
            text = " ".join(self._current_cell).strip()
            text = re.sub(r"\s+", " ", text)
            self._current_row.append(text)
            self._current_cell = []
            self._in_cell = False
        if tag == "tr" and self._in_row:
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = []
            self._in_row = False
        if tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._current_table = []
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)


def find_tallest_table(tables: List[List[List[str]]]) -> Optional[List[List[str]]]:
    for table in tables:
        if not table:
            continue
        header = " ".join(table[0]).lower()
        if "rank" in header and "name" in header and "height" in header:
            return table
    return None


def parse_table(table: List[List[str]], top_n: int) -> List[Dict[str, object]]:
    rows = table[1:]
    output: List[Dict[str, object]] = []
    for row in rows:
        if not row or len(row) < 4:
            continue
        rank_text = row[0].strip()
        if not rank_text.isdigit():
            continue
        rank = int(rank_text)
        if rank > top_n:
            continue
        name = row[1].strip()
        # Location cell usually at index 3 (Rank, Name, Image, Location, Height...)
        location_cell = row[3] if len(row) > 3 else ""
        match = COORD_RE.search(location_cell)
        if not match:
            # Try the whole row as fallback.
            match = COORD_RE.search(" ".join(row))
        if not match:
            continue
        lat = float(match.group(1))
        lon = float(match.group(2))
        output.append({"rank": rank, "name": name, "lat": lat, "lon": lon})
    output.sort(key=lambda r: r["rank"])
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract top N SF buildings with coordinates.")
    parser.add_argument("--top", type=int, default=25, help="Top N buildings.")
    parser.add_argument("--out", required=True, help="Output JSON path.")
    args = parser.parse_args()

    resp = requests.get(WIKI_URL, timeout=30, headers={"User-Agent": "lidar-sf/0.1 (local script)"})
    resp.raise_for_status()

    parser_obj = WikiTableParser()
    parser_obj.feed(resp.text)
    table = find_tallest_table(parser_obj.tables)
    if not table:
        raise SystemExit("Could not find tallest buildings table.")

    data = parse_table(table, args.top)
    if not data:
        raise SystemExit("No rows parsed from the tallest buildings table.")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {len(data)} buildings to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
