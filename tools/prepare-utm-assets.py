#!/usr/bin/env python3
"""Download verified UTM graphics libraries and shaders into build/, without installation."""
import argparse
from utm_assets import UTMAssets

if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(UTMAssets().prepare())
