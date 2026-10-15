from vehicles.type_mapping import normalize_vehicle_type, resolve_vehicle_type_for_ui

POLICY_LORRY = "lorry"
POLICY_DEFAULT = "default"


def resolve_vehicle_policy_key(vehicle) -> str:
    """
    Определяет policy key для ТС по типу/имени.
    Пока поддерживается только логика самосвалов.
    """
    if not vehicle:
        return POLICY_DEFAULT

    resolved_type = resolve_vehicle_type_for_ui(
        getattr(vehicle, "type", None),
        getattr(vehicle, "name", None),
        getattr(vehicle, "veh_id", None),
    )
    normalized = normalize_vehicle_type(resolved_type)

    if normalized == "Lorry":
        return POLICY_LORRY

    return POLICY_DEFAULT


def is_lorry_vehicle(vehicle) -> bool:
    return resolve_vehicle_policy_key(vehicle) == POLICY_LORRY
