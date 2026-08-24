import sqlite3
from pathlib import Path
from typing import List, Dict, Tuple

from process_scripts.sim_information import SimulatorInformation, SimData

class SimStat(SimulatorInformation):
    def __init__(self, dir_path: Path):
        self.file_path = dir_path / "sim.stats.sqlite3"
        self.conn = None
    
    # Open up sqlite3 connection with a path to the file_path
    # Once connection is established, return a connection
    def create_connection(self) -> bool:
        try:
            self.conn = sqlite3.connect(self.file_path)
            c = self.conn.cursor()
            c.execute("select * from `names`")
            c.close()
        except sqlite3.Error as e:
            fd = open("fails.txt", "a+")
            fd.write(f"{file_path.parent}\n")
            fd.close()
            print("error at", file_path, e)
            self.conn = None
            return False
        return True
   
    def get(self) -> List[SimData]:
        ''' Get values of model, with some timings
    
        Parameter
        ---------
        conn: An SQLite3 connection to the DB
        Desired_metric : Tuple[str, str] if empty list then all metrics are taken
            e.g. ("perforamnce_model", "elapsed_time") or ("L1-D", "load-misses")
        '''

        if self.conn is None:
            return

        cur = self.conn.cursor()
    
        cur.execute("SELECT * FROM (SELECT * FROM `values` WHERE prefixid=3) AS T1 LEFT JOIN (SELECT * FROM `values` WHERE prefixid=2) AS T2 on T1.nameid = T2.nameid AND T1.core = T2.core LEFT JOIN `names` on T1.nameid = `names`.nameid")
    
        #cur.execute("SELECT * FROM `values` WHERE prefixid = ? %s;" % namefilter, (prefixid[0],)) #get ROI_BEGIN
    
        rows = cur.fetchall()
    
        values = []
        # row: prefixid (event), nameid, core, value1, prefixid, objectid, core, value2, nameid, obj_name, metric_name
        for (prefixid1, nameid1, core1, value1, prefixid2, nameid2, core2, value2, nameid3, obj_name, metric_name) in rows:
            val = value1 - (value2 if value2 else 0)
            values.append(SimData("stats", obj_name, metric_name, core1, val))
        cur.close()
    
        return values
