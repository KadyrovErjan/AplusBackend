def build_file_url(request, file_field):
    """
    request: HttpRequest (из DRF serializer context)
    file_field: ImageField / FileField
    """
    if not file_field:
        return None

    if not request:
        return file_field.url

    return request.build_absolute_uri(file_field.url)
