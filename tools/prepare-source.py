#!/usr/bin/env python3
"""Print a dependency's prepared source path; Git initializes submodules separately."""
import argparse
from sources import Sources

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component')
    args = parser.parse_args()
    print(Sources().prepare(args.component))
