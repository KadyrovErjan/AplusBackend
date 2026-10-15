def _to_float(value):
    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if not value:
            return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick_preferred_numeric(data, keys):
    zero_candidate = None

    for key in keys:
        value = _to_float(data.get(key))
        if value is None:
            continue
        if value > 0:
            return value
        if zero_candidate is None:
            zero_candidate = value

    return zero_candidate


def extract_istatus_odometer_km(data):
    return _pick_preferred_numeric(
        data,
        ("общий_пробег", "total_run", "total_run_by_can", "run"),
    )


def extract_istatus_engine_hours(data):
    seconds = _pick_preferred_numeric(data, ("моточасы", "motorhours"))
    if seconds is None:
        return None
    return seconds / 3600.0


def extract_istatus_fuel_liters(data):
    return _pick_preferred_numeric(data, ("топливо", "fuel"))
