async def read_or_default(reader):
    try:
        return reader()
    except ValueError:
        return None
