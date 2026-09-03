import json
import sys
from pathlib import Path

from app.services.ocr_service import extract_text
from app.services.nlp_service import extract_fields


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: py -m tests.manual_nlp_test <path_to_file>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    extension = file_path.suffix.lower()

    raw_text = extract_text(file_path, extension)
    fields = extract_fields(raw_text)

    print("----- RAW OCR TEXT -----")
    print(raw_text)
    print("----- EXTRACTED FIELDS -----")
    print(json.dumps(fields.model_dump(), indent=2))


if __name__ == "__main__":
    main()