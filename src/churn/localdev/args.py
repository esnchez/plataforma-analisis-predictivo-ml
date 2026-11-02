"""Seed job arguments, kept apart from seed.py so the CLI can declare them without importing pandas."""

import argparse


def add_seed_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--customers", type=int, default=2000)
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument("--last-month", default="2026-09", help="last simulated month, YYYY-MM")
    parser.add_argument("--seed", type=int, default=42)
