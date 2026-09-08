#!/usr/bin/python3
"""Discover the configured VM's management address; identity is checked by SSH."""
from vm_session import address, settings

if __name__ == '__main__':
    print(address(settings()))
