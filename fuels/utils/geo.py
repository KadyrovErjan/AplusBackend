import math


def get_distance_m(lat1, lon1, lat2, lon2):
    """Haversine formula"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def is_point_in_circle(lat, lon, center_lat, center_lon, radius_m):
    if center_lat is None or center_lon is None or radius_m is None:
        return False
    return get_distance_m(lat, lon, center_lat, center_lon) <= radius_m


def normalize_polygon_coords(polygon_coords):
    """
    Приводит координаты полигона к формату [[lon, lat], ...].
    Поддерживает вложенность вида:
      - [[lon, lat], ...]
      - [[[lon, lat], ...]]
      - [[[[lon, lat], ...]]] (MultiPolygon с одним полигоном)
    """
    if not polygon_coords or not isinstance(polygon_coords, list):
        return []

    coords = polygon_coords
    while (
        isinstance(coords, list)
        and coords
        and isinstance(coords[0], list)
        and coords[0]
        and isinstance(coords[0][0], list)
    ):
        coords = coords[0]

    points = []
    for p in coords:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        try:
            x = float(p[0])
            y = float(p[1])
        except (TypeError, ValueError):
            continue
        points.append([x, y])

    return points


def is_point_in_polygon_normalized(lat, lon, polygon_coords):
    """
    Безопасная проверка попадания в полигон.
    Пытается как [lon, lat], так и [lat, lon] при необходимости.
    """
    points = normalize_polygon_coords(polygon_coords)
    if not points:
        return False

    if is_point_in_polygon(lat, lon, points):
        return True

    swapped = [[p[1], p[0]] for p in points]
    return is_point_in_polygon(lat, lon, swapped)


def find_zone_for_point(lat, lon, zones=None):
    """
    Возвращает FuelZone для точки (lat, lon) или None.
    Можно передать список zones, чтобы избежать повторных запросов.
    """
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None

    if zones is None:
        from fuels.models import FuelZone
        zones = FuelZone.objects.all()

    for zone in zones:
        if zone.zone_type == 2:
            if is_point_in_circle(lat, lon, zone.center_lat, zone.center_lon, zone.radius_m):
                return zone
            if is_point_in_circle(lat, lon, zone.center_lon, zone.center_lat, zone.radius_m):
                return zone
        elif zone.zone_type == 1:
            if is_point_in_polygon_normalized(lat, lon, zone.polygon_coords):
                return zone

    return None


def is_point_in_polygon(lat, lon, polygon_coords):
    """
    Ray casting algorithm.
    lat: Широта (Y)
    lon: Долгота (X)
    """
    if not polygon_coords:
        return False

    points = polygon_coords
    if len(points) > 0 and isinstance(points[0][0], list):
        points = points[0]

    num_vertices = len(points)

    x = lon
    y = lat

    inside = False

    p1 = points[0]
    p1x, p1y = p1[0], p1[1]

    for i in range(num_vertices + 1):
        p2 = points[i % num_vertices]
        p2x, p2y = p2[0], p2[1]

        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y

    return inside
