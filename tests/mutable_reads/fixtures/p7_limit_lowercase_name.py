# Documents a form the scan does not cover (see `--limits`): P7 looks for an UPPER_CASE constant name.
rules_path = "/srv/clone/docs/reference/rules.md"


def is_full_read(tin):
    return tin.get("file_path") == rules_path
