from dataclasses import dataclass

from django.db.models import Q


NO_ZONE_ID = 98293742


@dataclass(frozen=True)
class ZoneFilter:
    zone_ids: tuple[int, ...]
    include_outside: bool
    zone_name: str | None = None

    @property
    def has_filters(self) -> bool:
        return bool(self.zone_ids or self.include_outside or self.zone_name)


def _getlist_compat(params, key: str):
    if hasattr(params, "getlist"):
        values = params.getlist(key)
        if values:
            return values
    if hasattr(params, "get"):
        value = params.get(key)
    else:
        value = None
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def parse_zone_filter_params(params):
    """
    Поддерживает:
    - zone_id=12
    - zone_ids=12,13 или zone_ids=12&zone_ids=13
    - zone_name=АЗС-100 (legacy)
    """
    raw_zone_values = []
    for item in _getlist_compat(params, "zone_ids"):
        for chunk in str(item).split(","):
            token = chunk.strip()
            if token:
                raw_zone_values.append(token)

    if not raw_zone_values:
        zone_id_raw = params.get("zone_id") if hasattr(params, "get") else None
        zone_id_str = str(zone_id_raw).strip() if zone_id_raw is not None else ""
        if zone_id_str:
            raw_zone_values.append(zone_id_str)

    include_outside = False
    zone_ids = []
    for raw_value in raw_zone_values:
        if raw_value.lower() in {"all", "*"}:
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            return None, f"zone_ids must be integer values, got: {raw_value}"
        if parsed == NO_ZONE_ID:
            include_outside = True
        else:
            zone_ids.append(parsed)

    zone_ids = tuple(dict.fromkeys(zone_ids))

    zone_name_raw = params.get("zone_name") if hasattr(params, "get") else None
    zone_name = str(zone_name_raw or "").strip().replace('"', "").replace("'", "")
    if zone_ids or include_outside:
        zone_name = None
    if not zone_name:
        zone_name = None

    return ZoneFilter(zone_ids=zone_ids, include_outside=include_outside, zone_name=zone_name), None


def apply_zone_filter(events_qs, reports_qs, zone_filter: ZoneFilter):
    if not zone_filter or not zone_filter.has_filters:
        return events_qs, reports_qs

    if zone_filter.zone_ids or zone_filter.include_outside:
        events_q = Q()
        reports_q = Q()

        if zone_filter.zone_ids:
            events_q |= Q(zone_id__in=zone_filter.zone_ids)
            reports_q |= Q(zone_id__in=zone_filter.zone_ids)

        if zone_filter.include_outside:
            events_q |= Q(zone__isnull=True)
            reports_q |= Q(zone_id__isnull=True)

        return events_qs.filter(events_q), reports_qs.filter(reports_q)

    if zone_filter.zone_name:
        return (
            events_qs.filter(zone_name__icontains=zone_filter.zone_name),
            reports_qs.filter(zone_name__icontains=zone_filter.zone_name),
        )

    return events_qs, reports_qs
