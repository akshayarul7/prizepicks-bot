"""
Player Image Registry
----------------------
Maintains a persistent map of all player headshots across PrizePicks data files.
"""

import json
import os
from typing import Dict

IMAGE_REGISTRY_FILE = "player_images.json"


def build_or_update_image_registry() -> Dict[str, str]:
    registry = {}
    if os.path.exists(IMAGE_REGISTRY_FILE):
        try:
            with open(IMAGE_REGISTRY_FILE, "r") as f:
                registry = json.load(f)
        except Exception:
            pass

    # Scan all available PrizePicks JSON files to harvest headshot URLs
    files_to_scan = [
        "prizepicks_fresh.json",
        "prizepicks_live_cache.json",
    ]

    for fname in files_to_scan:
        if os.path.exists(fname):
            try:
                with open(fname, "r") as f:
                    data = json.load(f)
                    for item in data.get("included", []):
                        if item.get("type") == "new_player":
                            attrs = item.get("attributes", {})
                            name = attrs.get("name") or attrs.get("display_name")
                            url = attrs.get("image_url")
                            if name and url and name not in registry:
                                registry[name] = url
            except Exception:
                pass

    with open(IMAGE_REGISTRY_FILE, "w") as f:
        json.dump(registry, f, indent=2)

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
