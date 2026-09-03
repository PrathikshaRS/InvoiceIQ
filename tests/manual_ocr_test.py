import sys
from pathlib import Path

from app.services.ocr_service import extract_text


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: py -m tests.manual_ocr_test <path_to_file>")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    extension = file_path.suffix.lower()

    text = extract_text(file_path, extension)

    print("----- EXTRACTED TEXT -----")
    print(text)
    print("----- END -----")


if __name__ == "__main__":
    main()