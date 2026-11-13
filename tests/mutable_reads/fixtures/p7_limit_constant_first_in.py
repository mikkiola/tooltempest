# Documents a form the scan does not cover (see `--limits`): with the constant first, P7 needs `==` or `!=`
# and the word file_path or filepath, so `in` and a bare `path` are not seen.
RULES = "/srv/clone/docs/reference/rules.md"


def is_rules_read(file_path):
    return RULES in (file_path,)
