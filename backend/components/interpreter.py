from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..ports import ICoreDumpParser
from ..coredump_interpreter import generate_coredump_report_docker


class DockerCoredumpParser(ICoreDumpParser):
    def generate_report(
        self,
        raw_path: Path,
        elf_path: Path,
        out_dir: Path,
        chip_type: Optional[str],
    ) -> Path:
        return generate_coredump_report_docker(
            coredump_path=raw_path,
            elf_path=elf_path,
            output_dir=out_dir,
            chip_type=chip_type,
        )


def create_parser() -> DockerCoredumpParser:
    return DockerCoredumpParser()

__all__ = ["DockerCoredumpParser", "create_parser"]


