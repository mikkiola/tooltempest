RULES = "/srv/clone/docs/reference/rules.md"


def is_full_read(tin):
    return tin.get("file_path") == RULES and tin.get("offset") in (None, 0, 1)
