import subprocess
from pathlib import Path
import importlib.util
from typing import List, Dict

import process_scripts.const as const
from process_scripts.sim_information import SimulatorInformation, SimData

class SimMcPat(SimulatorInformation):
    def __init__(self, dir_path: Path):
        self.dir_path = dir_path
        self.time_in_seconds = 0

    def set_time(self, time_in_seconds: float):
        self.time_in_seconds = time_in_seconds

    # ---------------------------------------------- Get McPat numbers ---------------------------------------------------
    def power_stack(power_dat, powertype = 'total', nocollapse = False):
      def getpower(powers, key = None):
        def getcomponent(suffix):
          if key: return powers.get(key+'/'+suffix, 0)
          else: return powers.get(suffix, 0)
        if powertype == 'dynamic':
          return getcomponent('Runtime Dynamic')
        elif powertype == 'static':
          return getcomponent('Subthreshold Leakage with power gating') + getcomponent('Gate Leakage')
        elif powertype == 'total':
          return getcomponent('Runtime Dynamic') + getcomponent('Subthreshold Leakage with power gating') + getcomponent('Gate Leakage')
        elif powertype == 'peak':
          return getcomponent('Peak Dynamic') + getcomponent('Subthreshold Leakage with power gating') + getcomponent('Gate Leakage')
        elif powertype == 'peakdynamic':
          return getcomponent('Peak Dynamic')
        elif powertype == 'area':
          return getcomponent('Area') + getcomponent('Area Overhead')
        else:
          raise ValueError('Unknown powertype %s' % powertype)
      data = {
        'l2':               sum([ getpower(cache) for cache in power_dat.get('L2', []) ])  # shared L2
                            + sum([ getpower(core, 'L2') for core in power_dat['Core'] ]), # private L2
        'l3':               sum([ getpower(cache) for cache in power_dat.get('L3', []) ]),
        'nuca':             sum([ getpower(cache) for cache in power_dat.get('NUCA', []) ]),
        'noc':              getpower(power_dat['Processor'], 'Total NoCs'),
        'dram':             getpower(power_dat['DRAM']),
        'core':             sum([ getpower(core, 'Execution Unit/Instruction Scheduler')
                                  + getpower(core, 'Execution Unit/Register Files')
                                  + getpower(core, 'Execution Unit/Results Broadcast Bus')
                                  + getpower(core, 'Renaming Unit')
                                  for core in power_dat['Core']
                                ]),
        'core-ifetch':      sum([ getpower(core, 'Instruction Fetch Unit/Branch Predictor')
                                  + getpower(core, 'Instruction Fetch Unit/Branch Target Buffer')
                                  + getpower(core, 'Instruction Fetch Unit/Instruction Buffer')
                                  + getpower(core, 'Instruction Fetch Unit/Instruction Decoder')
                                  for core in power_dat['Core']
                                ]),
        'icache':           sum([ getpower(core, 'Instruction Fetch Unit/Instruction Cache') for core in power_dat['Core'] ]),
        'dcache':           sum([ getpower(core, 'Load Store Unit/Data Cache') for core in power_dat['Core'] ]),
        'core-alu-complex': sum([ getpower(core, 'Execution Unit/Complex ALUs') for core in power_dat['Core'] ]),
        'core-alu-fp':      sum([ getpower(core, 'Execution Unit/Floating Point Units') for core in power_dat['Core'] ]),
        'core-alu-int':     sum([ getpower(core, 'Execution Unit/Integer ALUs') for core in power_dat['Core'] ]),
        'core-mem':         sum([ getpower(core, 'Load Store Unit/LoadQ')
                                  + getpower(core, 'Load Store Unit/StoreQ')
                                  + getpower(core, 'Memory Management Unit')
                                  for core in power_dat['Core']
                                ]),
      }
      data['core-other'] = getpower(power_dat['Processor']) - (sum(data.values()) - data['dram'])
      data['total'] = sum(data.values())
      return data
      #return buildstack.merge_items({ 0: data }, all_items, nocollapse = nocollapse)
    
    def get(self) -> List[SimData]:
        cmd = [const.McPat_PATH, "-d", self.dir_path, "--no-graph", "--no-text"]
        mcpat_output_file = self.dir_path / "power.py"
        if(not mcpat_output_file.exists()):
            subprocess.run(cmd, cwd=self.dir_path)

        spec = importlib.util.spec_from_file_location("power", mcpat_output_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        power_values = SimMcPat.power_stack(mod.power, "total")
        energy_values = {k: float(v) * self.time_in_seconds for k,v in power_values.items()}
        area_values = SimMcPat.power_stack(mod.power, "area")

        values = []
        for (metric_to_use, dict_use) in [("Power", power_values), ("Energy", energy_values), ("Area", area_values)]:
             for k, v in dict_use.items():
                 values.append(SimData("mcpat", metric_to_use, k, None, v))
                
        return values
