from collections import defaultdict
from datetime import timedelta

def group_fuel_reports(reports, window_minutes=10):
    groups = []
    window = timedelta(minutes=window_minutes)

    for r in reports:
        placed = False
        for g in groups:
            if abs(r.created_at - g["ts"]) <= window:
                g["reports"].append(r)
                placed = True
                break

        if not placed:
            groups.append({
                "ts": r.created_at,
                "reports": [r],
            })

    return groups
