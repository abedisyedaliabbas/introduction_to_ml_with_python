#!/usr/bin/env python3
"""
Generate Gaussian input files from existing geometry `.com` files.

This script generalizes the original Step-6 generator by allowing the
user to customise the step number and mode (cLR or SS) as well as
arbitrary route-section keywords.

Usage remains similar: place the script in the directory containing
geometry `.com` files and run it.  The generated files will be placed in
`Config.out_dir`.  A master submission script `<step>sub.sh` is also
created for batch submission.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence, Tuple


@dataclass
class Config:
    """Configuration for input generation."""
    # General step information
    step: int = 6                     # Step number used in names/scripts
    out_dir: str = "T1"               # Output folder for generated files
    prefix: str = "T1_"              # Prefix for generated filenames

    # Gaussian settings
    functional: str = "m062x"
    basis: str = "def2SVP"
    solvent_model: str = "SMD"
    solvent_name: str = "DMSO"
    charge_details: bool = False      # Whether to include extra pop settings
    route_keywords: str = ""         # Additional keywords for the route line

    # Solvation mode: "cLR" or "SS"
    mode: str = "cLR"

    # Use TD=(Read,...) with %oldchk? If True, requires prior chk.
    use_read_density: bool = False
    oldchk_pattern: str = "05{basename}.chk"

    # Resources / scheduler
    cpu_kw: str = "%nprocshared=32"
    mem_kw: str = "%mem=64GB"
    link0_extra: List[str] = field(default_factory=list)
    gauss_bin: str = "g16"
    queue: str = "normal"
    walltime: str = "24:00:00"
    project_id: str = "15002108"
    clock_on: bool = True


def info(msg: str) -> None:
    print(msg, flush=True)


def write_lines(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="\n") as f:
        for line in lines:
            if line is None:
                continue
            f.write(str(line).rstrip("\r\n"))
            f.write("\n")


def parse_cpu_mem(cpu_kw: str, mem_kw: str) -> Tuple[str, str]:
    m_cpu = re.search(r"nprocshared\s*=\s*(\d+)", cpu_kw, re.IGNORECASE)
    if not m_cpu:
        raise ValueError(f"Could not parse CPU count from '{cpu_kw}'")
    cpu = m_cpu.group(1)

    m_mem = re.search(r"%mem\s*=\s*([0-9]+\s*[A-Za-z]+)", mem_kw, re.IGNORECASE)
    if not m_mem:
        mem = mem_kw.replace("%mem=", "").strip()
    else:
        mem = m_mem.group(1).replace(" ", "")
    return cpu, mem


def sanitize_token(s: str) -> str:
    """Lowercase and replace any non-alphanumeric with single underscore."""
    s = s.strip().lower()
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s)
    return s.strip("_")


def extract_charge_coords(com_path: Path) -> Tuple[str, List[str]]:
    """Extract charge/multiplicity and coordinates from a `.com` file."""
    raw = com_path.read_text().splitlines()
    empties = [i for i, line in enumerate(raw) if not line.strip()]
    coords: List[str] = []
    if len(empties) >= 3 and (empties[1] - empties[0] == 2):
        start = empties[1] + 1
        end = empties[2]
        coords = raw[start:end]
    else:
        if len(empties) >= 2:
            coords = raw[empties[1] + 1 :]
        else:
            coords = raw[:]

    cs = ""
    atom_pat = re.compile(r"^[A-Za-z]{1,2}\s+[-\d]")
    for L in coords:
        if L.strip() and not atom_pat.match(L.strip()):
            cs = L.strip()
            break
    if not cs or not re.match(r"^\s*-?\d+\s+-?\d+\s*$", cs):
        cs = "0 1"

    while coords and (not coords[0].strip() or re.match(r"^\s*-?\d+\s+-?\d+\s*$", coords[0])):
        coords.pop(0)

    return cs, coords


def gaussian_solvent_strings(cfg: Config) -> Tuple[str, str]:
    solvent = f"scrf={cfg.solvent_model}, solvent={cfg.solvent_name}"
    solvent2 = f"solvent={cfg.solvent_name}"
    return solvent, solvent2


def write_pbs(output_path: Path, job_name: str, cfg: Config) -> None:
    cpu, mem = parse_cpu_mem(cfg.cpu_kw, cfg.mem_kw)
    lines = [
        "#!/bin/bash",
        f"#PBS -q {cfg.queue}",
        f"#PBS -l select=1:ncpus={cpu}:mpiprocs={cpu}:mem={mem}",
    ]
    if cfg.clock_on:
        lines.append(f"#PBS -l walltime={cfg.walltime}")
        lines.append(f"#PBS -P {cfg.project_id}")
    else:
        lines.append(" ")
        lines.append(f"#PBS -P {Path(cfg.out_dir).name}")
    lines += [
        f"#PBS -N {job_name}",
        f"#PBS -o {job_name}.o",
        f"#PBS -e {job_name}.e",
        "#PBS -m ea",
        "##PBS -M your_email@example.com",
        "",
        "cd $PBS_O_WORKDIR",
        "np=$(cat ${PBS_NODEFILE} | wc -l)",
        "",
        f"{cfg.gauss_bin} < {job_name}.com > {job_name}.log",
    ]
    write_lines(output_path, lines)


def make_route(cfg: Config, solvent: str) -> str:
    if cfg.mode.lower() == "ss":
        scrf_tail = "ExternalIteration, NonEquilibrium=Save"
    else:
        scrf_tail = "CorrectedLR, NonEq=Save"

    if cfg.use_read_density:
        td_part = "TD=(Read, NStates=3, Root=2) "
    else:
        td_part = "TD=(NStates=3, Root=2) "

    extras = f"{cfg.route_keywords} " if cfg.route_keywords else ""
    route = (
        f"# {cfg.functional}/{cfg.basis} {extras}geom=check guess=read "
        f"{td_part}SCRF=({solvent[5:]}, {scrf_tail})"
    )
    return route


def make_route_with_geom(cfg: Config, solvent: str) -> str:
    if cfg.mode.lower() == "ss":
        scrf_tail = "ExternalIteration, NonEquilibrium=Save"
    else:
        scrf_tail = "CorrectedLR, NonEq=Save"

    if cfg.use_read_density:
        td_part = "TD=(Read, NStates=3, Root=2) "
    else:
        td_part = "TD=(NStates=3, Root=2) "

    extras = f"{cfg.route_keywords} " if cfg.route_keywords else ""
    route = (
        f"# {cfg.functional}/{cfg.basis} {extras}{td_part}"
        f"SCRF=({solvent[5:]}, {scrf_tail})"
    )
    return route


def main() -> None:
    cfg = Config()
    out_dir = Path.cwd() / cfg.out_dir
    out_dir.mkdir(exist_ok=True)

    func_tok = sanitize_token(cfg.functional)
    basis_tok = sanitize_token(cfg.basis)
    solv_tok = sanitize_token(cfg.solvent_name)
    tag = f"{func_tok}_{basis_tok}_{solv_tok}"

    solvent, solvent2 = gaussian_solvent_strings(cfg)

    com_files = sorted(Path(".").glob("*.com"))
    if not com_files:
        raise FileNotFoundError("No '.com' files found in current directory. Put your geometries here.")

    info(f"Found {len(com_files)} .com file(s). Writing inputs to {out_dir} ...")

    created_jobs: List[str] = []

    for p in com_files:
        original_name = p.stem
        cs, coords = extract_charge_coords(p)

        base_core = f"{cfg.prefix}{original_name}"
        base_with_tag = f"{base_core}_{tag}"
        job_name = f"step{cfg.step}_{sanitize_token(original_name)}_{tag}"

        lines: List[str] = []
        lines += [cfg.cpu_kw, cfg.mem_kw]
        lines += cfg.link0_extra

        if cfg.use_read_density:
            oldchk_name = cfg.oldchk_pattern.format(basename=original_name, prefix=cfg.prefix)
            lines.append(f"%oldchk={oldchk_name}")
        lines.append(f"%chk={base_with_tag}.chk")

        title = (
            f"Step {cfg.step} SP {cfg.functional}/{cfg.basis} SCRF=(solvent={cfg.solvent_name}) | {original_name}"
        )
        if cfg.use_read_density:
            lines.append(make_route(cfg, solvent))
            lines += ["", title, "", cs]
        else:
            lines.append(make_route_with_geom(cfg, solvent))
            lines += ["", title, "", cs]
            lines += coords

        write_lines(out_dir / f"{base_with_tag}.com", lines)
        write_pbs(out_dir / f"{base_with_tag}.sh", base_with_tag, cfg)
        created_jobs.append(base_with_tag)

    sub_name = f"{cfg.step:02d}sub.sh"
    sub_lines = [f"qsub {name}.sh" for name in created_jobs]
    write_lines(out_dir / sub_name, sub_lines)

    info(f"Done. Created {sub_name} for batch submission.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
