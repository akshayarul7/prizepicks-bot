"""
Player Image Registry
----------------------
Maintains a persistent map of all player headshots across PrizePicks data files.
"""

import json
import os
from typing import Dict

IMAGE_REGISTRY_FILE = "player_images.json"


def harvest_images_from_data(prizepicks_json: Dict) -> Dict[str, str]:
    """Harvests new player headshots directly from in-memory API response."""
    registry = {}
    if os.path.exists(IMAGE_REGISTRY_FILE):
        try:
            with open(IMAGE_REGISTRY_FILE, "r") as f:
                registry = json.load(f)
        except Exception:
            pass

    updated = False
    for item in prizepicks_json.get("included", []):
        if item.get("type") == "new_player":
            attrs = item.get("attributes", {})
            name = attrs.get("name") or attrs.get("display_name")
            url = attrs.get("image_url")
            if name and url and registry.get(name) != url:
                registry[name] = url
                updated = True

    if updated:
        with open(IMAGE_REGISTRY_FILE, "w") as f:
            json.dump(registry, f, indent=2)

    return registry


def build_or_update_image_registry() -> Dict[str, str]:
    """
    Loads indexed player headshots directly from player_images.json (Ultra-low RAM: ~18MB).
    Does NOT scan giant 40MB raw files into memory.
    """
    registry = {}
    if os.path.exists(IMAGE_REGISTRY_FILE):
        try:
            with open(IMAGE_REGISTRY_FILE, "r") as f:
                registry = json.load(f)
        except Exception:
            pass

    return registry


def get_player_image_url(player_name: str) -> str:
    registry = build_or_update_image_registry()
    return registry.get(player_name, "")


if __name__ == "__main__":
    reg = build_or_update_image_registry()
    print(f"Player image registry updated: {len(reg)} players indexed.")
    targets = ["Jayson Tatum", "Cade Cunningham", "Victor Wembanyama", "Quentin Grimes", "Payton Pritchard", "Donovan Clingan"]
    for t in targets:
        print(f"  {t}: {reg.get(t)}")
