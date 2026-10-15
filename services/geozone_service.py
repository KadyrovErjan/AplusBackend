import json
from vehicles.models import Geozone


def point_in_polygon(lat, lon, polygon):
    """
    Ray casting algorithm
    polygon = [(lat, lon), (lat, lon), ...]
    """
    inside = False
    j = len(polygon) - 1

    for i in range(len(polygon)):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[j]

        intersect = (
            ((lon_i > lon) != (lon_j > lon)) and
            (lat < (lat_j - lat_i) * (lon - lon_i) / (lon_j - lon_i + 1e-9) + lat_i)
        )
        if intersect:
            inside = not inside

        j = i

    return inside


def find_zone_for_point(lat: float, lon: float):
    """
    Возвращает Geozone или None
    """
    zones = Geozone.objects.exclude(geometry__isnull=True)

    for z in zones:
        try:
            geom = json.loads(z.geometry)
        except Exception:
            continue

        if geom.get("type") == "Polygon":
            rings = geom["coordinates"]

        elif geom.get("type") == "MultiPolygon":
            rings = geom["coordinates"][0]

        else:
            continue

        outer_ring = [
            (coord[1], coord[0]) 
            for coord in rings[0]
        ]

        if point_in_polygon(lat, lon, outer_ring):
            return z

    return None
