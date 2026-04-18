MAX_LENGTH_TRUNCATE_CONTENT = 20000

BASE_BUILTIN_MODULES = [
    "collections",
    "datetime",
    "itertools",
    "math",
    "queue",
    "random",
    "re",
    "stat",
    "statistics",
    "time",
    "unicodedata",
]


def truncate_content(content: str, max_length: int = MAX_LENGTH_TRUNCATE_CONTENT) -> str:
    if len(content) <= max_length:
        return content
    return (
        content[: max_length // 2]
        + f"\n..._This content has been truncated to stay below {max_length} characters_...\n"
        + content[-max_length // 2 :]
    )
