RULES_PATH = "/srv/clone/docs/reference/rules.md"


def is_rules_read(tin):
    return tin.get("file_path") in (RULES_PATH,)
