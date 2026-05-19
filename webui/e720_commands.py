from __future__ import annotations

from typing import Dict, List, Tuple

# Front-panel keys from measure_device/e720/driver.py menu map.
PANEL_COMMANDS: Dict[str, int] = {
    'Menu': 0,
    'Right': 1,
    'Z': 2,
    'R': 3,
    'Down': 4,
    'Enter': 5,
    'Up': 6,
    'L': 7,
    '0': 8,
    'Left': 9,
    'I': 10,
    'C': 11,
    'Offset': 12,
    'Freq': 13,
    'Level': 14,
    'Mode': 15,
}

# Delphi UI button mapping (Unit1.pas).
LEGACY_BUTTON_COMMANDS: Dict[str, int] = {
    'Legacy Btn 0': 0,
    'Legacy Btn 1': 1,
    'Legacy Btn 2': 2,
    'Legacy Btn 3': 3,
    'Legacy Btn 4': 4,
    'Legacy Btn 5': 5,
    'Legacy Btn 6': 6,
    'Legacy Btn 7': 7,
    'Legacy Btn 8': 8,
    'Legacy Btn 9': 9,
    'Legacy Btn 10': 10,
    'Legacy Btn 11': 11,
    'Legacy Btn 12': 12,
    'Legacy Btn 13': 13,
    'Legacy Btn 14': 14,
    'Legacy Btn 15': 15,
    'Trigger measure (1)': 1,
}


def command_choices() -> List[Tuple[str, int]]:
    choices: List[Tuple[str, int]] = []
    for label, value in PANEL_COMMANDS.items():
        choices.append((f'Panel: {label}', value))
    for label, value in LEGACY_BUTTON_COMMANDS.items():
        if (label, value) not in choices:
            choices.append((label, value))
    return choices
