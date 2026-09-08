#!/usr/bin/python3
"""Publish only fresh, validated host battery telemetry to the virtual driver."""
import json,pathlib,time
source=pathlib.Path('/run/linuxhost/status.json')
state=pathlib.Path('/sys/devices/platform/linuxhost-battery/state')
while True:
 try:
  if time.time()-source.stat().st_mtime>20:raise ValueError('stale telemetry')
  data=json.loads(source.read_text());b=data['battery']
  if data.get('ok') is not True:raise ValueError('host unavailable')
  current=b['Current Capacity'];maximum=b['Max Capacity'];charging=b['Is Charging'];power=b['Power Source State']
  if type(current) is not int or type(maximum) is not int or maximum<=0 or not 0<=current<=maximum or type(charging) is not bool or power not in ('AC Power','Battery Power'):raise ValueError('invalid telemetry')
  state.write_text(f'1 {int(power=="AC Power")} {round(current*100/maximum)} {int(charging)}\n')
 except (OSError,KeyError,ValueError,TypeError):
  pass # The driver's watchdog marks missing/stale telemetry unavailable.
 time.sleep(5)
