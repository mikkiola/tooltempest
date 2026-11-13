DEFAULT_SOURCE = "~/.cache/tooltempest/repo"


def open_store(object_store_class):
    # Only Git objects of the source are read, never its working tree.
    return object_store_class(DEFAULT_SOURCE)
