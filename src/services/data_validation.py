"""Simple data validation for Oliviers JSON datasets.
Reads the files in ../Oliviers and prints a short report (counts, sample ids, schema checks).
"""
from pathlib import Path
import json

# Project root is two levels above this file: src/services -> src -> project root
DATA_DIR = Path(__file__).resolve().parents[2] / "Oliviers"
FILES = ["parcelles_OlivierExtensif.json", "parcellesOliviersIntensifs.json"]

def inspect_file(path: Path):
    if not path.exists():
        print(f"MISSING: {path}")
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"ERROR reading {path}: {e}")
        return

    # Try common shapes
    parcels = None
    if isinstance(data, dict):
        # common keys: 'parcels', 'features', or geojson 'type'/'features'
        if 'parcels' in data:
            parcels = data['parcels']
        elif 'features' in data:
            parcels = data['features']
        elif 'type' in data and data.get('type').lower() == 'featurecollection' and 'features' in data:
            parcels = data['features']
        else:
            # maybe it's directly a mapping of id->parcel
            # detect list-like
            for v in data.values():
                if isinstance(v, list):
                    parcels = v
                    break
    elif isinstance(data, list):
        parcels = data

    if parcels is None:
        print(f"UNKNOWN FORMAT: {path} — top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        return

    count = len(parcels)
    print(f"FILE: {path.name} — {count} items")

    # Sample checks
    samples = parcels[:3]
    for i, s in enumerate(samples, 1):
        # Attempt to find id and polygon/geometry
        pid = s.get('id') if isinstance(s, dict) else None
        poly = None
        if isinstance(s, dict):
            poly = s.get('polygone') or s.get('geometry') or s.get('polygon')
        print(f"  sample {i}: id={pid} poly_present={bool(poly)} keys={list(s.keys()) if isinstance(s, dict) else type(s)}")

    # Basic aggregate checks
    ids = []
    missing_poly = 0
    for p in parcels:
        if isinstance(p, dict):
            ids.append(p.get('id'))
            if not (p.get('polygone') or p.get('geometry') or p.get('polygon')):
                missing_poly += 1
    unique_ids = len(set([i for i in ids if i is not None]))
    print(f"  unique ids: {unique_ids} (missing polygon for {missing_poly} items)\n")


if __name__ == '__main__':
    print(f"DATA_DIR: {DATA_DIR}")
    for fname in FILES:
        inspect_file(DATA_DIR / fname)
    # Also list any other json files in the directory
    others = [p.name for p in DATA_DIR.glob('*.json') if p.name not in FILES]
    if others:
        print("Other JSON files found:")
        for o in others:
            print(f"  - {o}")
