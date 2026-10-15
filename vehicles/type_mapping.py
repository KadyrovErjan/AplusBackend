import re


VEHICLE_TYPE_LABELS = {
    "Agrimotor": "Виброкаток",
    "Lorry": "Самосвал",
    "Excavator": "Экскаватор",
    "Pump": "Насос",
    "Crane": "Кран",
    "Crane Arm": "Кран-манипулятор",
    "Car": "Легковой автомобиль",
    "Autohydraulic": "Автогидравлика",
    "Mixer": "Бетономешалка",
    "Light Truck": "Спринтер",
    "Front Loader": "Погрузчик",
    "Truck": "Грузовик",
}


_TYPE_ALIASES = {
    "agrimotor": "Agrimotor",
    "аgrimotor": "Agrimotor", 
    "lorry": "Lorry",
    "самосвал": "Lorry",
    "хово самосвал": "Lorry",
    "excavator": "Excavator",
    "pump": "Pump",
    "crane": "Crane",
    "crane arm": "Crane Arm",
    "car": "Car",
    "autohydraulic": "Autohydraulic",
    "mixer": "Mixer",
    "light truck": "Light Truck",
    "front loader": "Front Loader",
    "truck": "Truck",
}


REVERSE_VEHICLE_LABELS = {v: k for k, v in VEHICLE_TYPE_LABELS.items()}


# Optional hard overrides for known bad source rows from Incomtek.
VEHICLE_TYPE_OVERRIDES_BY_ID = {
    # 9004: "Agrimotor",
    # 9059: "Excavator",
}

_GENERIC_SOURCE_TYPES = {"Lorry", "Truck", "Car", None}

_LAT_TO_CYR = str.maketrans({
    "a": "а",
    "b": "в",
    "c": "с",
    "e": "е",
    "h": "н",
    "k": "к",
    "m": "м",
    "o": "о",
    "p": "р",
    "t": "т",
    "x": "х",
    "y": "у",
})

_NAME_TYPE_HINTS = [
    ("Crane Arm", ("манипулятор",)),
    ("Agrimotor", ("виброкаток", "agrimotor")),
    ("Excavator", ("экскаватор", "excavator")),
    ("Front Loader", ("погрузчик", "loader")),
    ("Crane", ("автокран", "кран", "crane")),
    ("Mixer", ("бетономеш", "миксер", "mixer")),
    ("Autohydraulic", ("помпа", "autohydraulic")),
    ("Pump", ("насос", "pump")),
    ("Light Truck", ("спринтер", "sprinter")),
    ("Lorry", ("самосвал", "хово", "howo", "lorry")),
]


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_vehicle_type(raw_type):
    if raw_type is None:
        return None

    cleaned = _normalize_spaces(str(raw_type))
    if not cleaned:
        return None

    return _TYPE_ALIASES.get(cleaned.lower(), cleaned)


def get_vehicle_type_label(raw_type):
    normalized = normalize_vehicle_type(raw_type)
    if not normalized:
        return None
    return VEHICLE_TYPE_LABELS.get(normalized, normalized)


def infer_vehicle_type_from_name(name):
    if not name:
        return None

    lowered = _normalize_spaces(str(name).lower())
    if not lowered:
        return None

    candidates = {lowered, lowered.translate(_LAT_TO_CYR)}

    for target_type, hints in _NAME_TYPE_HINTS:
        if any(hint in candidate for candidate in candidates for hint in hints):
            return target_type
    return None


def resolve_vehicle_type_for_ui(raw_type, name=None, veh_id=None):
    if veh_id in VEHICLE_TYPE_OVERRIDES_BY_ID:
        return VEHICLE_TYPE_OVERRIDES_BY_ID[veh_id]

    normalized = normalize_vehicle_type(raw_type)
    inferred = infer_vehicle_type_from_name(name)

    if inferred and normalized in _GENERIC_SOURCE_TYPES:
        return inferred

    return normalized or inferred
