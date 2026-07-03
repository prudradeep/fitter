from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.generate_open_conversation_selection_test_cases import (
    OUTPUT_FILE as TEST_CASES_FILE,
    create_workbook,
)
from tests.run_open_conversation_selection_cases import (
    OUTPUT_FILE as RESULTS_FILE,
    run_cases,
    write_results_workbook,
)


def main() -> None:
    test_cases_path = create_workbook(Path.cwd() / TEST_CASES_FILE)
    results = asyncio.run(run_cases())
    results_path = write_results_workbook(results, Path.cwd() / RESULTS_FILE)

    passed = sum(1 for item in results if item["Status"] == "Pass")
    failed = len(results) - passed
    print(f"Created Excel test-case file: {test_cases_path}")
    print(f"Created selection test result file: {results_path}")
    print(f"Total: {len(results)} | Passed: {passed} | Failed: {failed}")


if __name__ == "__main__":
    main()
