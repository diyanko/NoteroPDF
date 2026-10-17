from pathlib import Path

# Explicit document types supported by Notion's File Upload API.
_SOURCE_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".json": "application/json",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".zip": "application/zip",
}


def source_content_type(path: str) -> str | None:
    return _SOURCE_TYPES.get(Path(path).suffix.lower())
