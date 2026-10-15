from datetime import timedelta

TIME_WINDOW = timedelta(minutes=10)


def group_fuel_reports(reports):
    """
    Группирует FuelReport по времени (±10 минут)
    reports: list[FuelReport] отсортированы по created_at
    """
    groups = []

    for report in reports:
        placed = False

        for g in groups:
            if abs(report.created_at - g["ts"]) <= TIME_WINDOW:
                g["reports"].append(report)
                placed = True
                break

        if not placed:
            groups.append({
                "ts": report.created_at,
                "reports": [report],
            })

    return groups
