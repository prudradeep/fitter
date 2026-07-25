from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.generate_hazard_creation_test_cases import (
    OUTPUT_FILE as TEST_CASES_FILE,
    create_workbook,
)
from tests.run_hazard_creation_cases import (
    DEFAULT_MODELS,
    result_filename_for_model,
    run_cases_for_model,
    write_results_workbook,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and run hazard creation regression workbooks."
    )
    parser.add_argument("--models", nargs="+", help="Ollama model names to test.")
    parser.add_argument("--limit", type=int, help="Optional case limit per model.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    test_cases_path = create_workbook(Path.cwd() / TEST_CASES_FILE)
    print(f"Created Excel hazard-creation test-case file: {test_cases_path}")

    models = args.models or DEFAULT_MODELS
    for model in models:
        results = asyncio.run(run_cases_for_model(model, limit=args.limit))
        results_path = write_results_workbook(
            results,
            Path.cwd() / result_filename_for_model(model),
        )
        passed = sum(1 for item in results if item["Status"] == "Pass")
        failed = len(results) - passed
        print(f"Created hazard creation test result file: {results_path}")
        print(f"Model: {model} | Total: {len(results)} | Passed: {passed} | Failed: {failed}")


if __name__ == "__main__":
    main()
