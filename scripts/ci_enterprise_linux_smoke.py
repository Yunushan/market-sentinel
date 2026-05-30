from __future__ import annotations

import argparse
from collections.abc import Callable

from verify import (
    check_dependency_imports,
    check_python_version,
    run_adapter_catalog_check,
    run_blockers_doc_check,
    run_ci_cd_workflow_check,
    run_compile_check,
    run_config_example_check,
    run_fixture_check,
    run_pip_check,
    run_project_metadata_check,
    run_readme_matrix_check,
)


def run_checks(label: str) -> None:
    checks: tuple[Callable[[], None], ...] = (
        check_python_version,
        check_dependency_imports,
        run_pip_check,
        run_compile_check,
        run_adapter_catalog_check,
        run_project_metadata_check,
        run_config_example_check,
        run_readme_matrix_check,
        run_blockers_doc_check,
        run_fixture_check,
        run_ci_cd_workflow_check,
    )
    print(f"[info] Enterprise Linux container smoke: {label}")
    for check in checks:
        check()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CI smoke checks inside Enterprise Linux containers.")
    parser.add_argument("--label", default="enterprise-linux", help="Human-readable platform label.")
    args = parser.parse_args()
    run_checks(args.label)


if __name__ == "__main__":
    main()
