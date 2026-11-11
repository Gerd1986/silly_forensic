"""Conservative structural sequential-circuit analysis.

Operator-confirmed DFF/MUX/gate annotations are required. No claims of automatic
recognition from SEM pixels or that a 16-state machine is a JTAG TAP.
"""
import re
try:
    from .reconstruction import reconstruct
except ImportError:
    from reconstruction import reconstruct

KINDS=("DFF","MUX2","AND2","OR2","XOR2","NOT","NAND2","NOR2")
PINS={"DFF":("D","Q","CLK"),"MUX2":("A","B","S","Y"),
      "AND2":("A","B","Y"),"OR2":("A","B","Y"),
      "XOR2":("A","B","Y"),"NOT":("A","Y"),
      "NAND2":("A","B","Y"),"NOR2":("A","B","Y")}
_NAME=re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

def ensure_logic_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS logic_cells (
       id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
       kind TEXT NOT NULL, layer TEXT NOT NULL, note TEXT NOT NULL DEFAULT '')""")
    db.execute("""CREATE TABLE IF NOT EXISTS logic_pins (
       cell_id INTEGER NOT NULL, pin TEXT NOT NULL, trace_id INTEGER NOT NULL,
       PRIMARY KEY(cell_id,pin))""")

def add_logic_cell(db,name,kind,layer,pins,note=""):
    ensure_logic_schema(db)
    kind=kind.upper()
    if not _NAME.fullmatch(name) or kind not in KINDS or set(pins)!=set(PINS[kind]):
        raise ValueError("Invalid logic cell name, kind or pin mapping")
    for pin,trace in pins.items():
        row=db.execute("SELECT status FROM trace_candidates WHERE id=?",(int(trace),)).fetchone()
        if not row or row[0]!="accepted":
            raise ValueError(f"{pin}: trace #{trace} must be accepted")
    cur=db.execute("INSERT INTO logic_cells(name,kind,layer,note) VALUES (?,?,?,?)",
                   (name,kind,layer,note))
    db.executemany("INSERT INTO logic_pins(cell_id,pin,trace_id) VALUES (?,?,?)",
                   [(cur.lastrowid,pin,int(trace)) for pin,trace in pins.items()])

def resolved_cells(db):
    ensure_logic_schema(db)
    netmap={}
    for net in reconstruct(db):
        for member in net["members"]:
            if member["kind"]=="trace":
                netmap[member["id"]]=net["name"]
    result=[]
    for ident,name,kind,layer,note in db.execute(
            "SELECT id,name,kind,layer,note FROM logic_cells ORDER BY id"):
        pins={pin:{"trace_id":tid,"net":netmap.get(tid)}
              for pin,tid in db.execute(
                  "SELECT pin,trace_id FROM logic_pins WHERE cell_id=?",(ident,))}
        result.append({"id":ident,"name":name,"kind":kind,"layer":layer,
                       "pins":pins,"complete":all(pins.get(p,{}).get("net") for p in PINS[kind]),
                       "note":note})
    return result

def analyze_sequential(db):
    cells=[c for c in resolved_cells(db) if c["complete"]]
    dffs=[c for c in cells if c["kind"]=="DFF"]
    by_clock={}
    for d in dffs:
        by_clock.setdefault(d["pins"]["CLK"]["net"],[]).append(d)
    groups=[]
    chains=[]
    for clock,items in sorted(by_clock.items()):
        q_to_cells={}
        for cell in items:
            q_to_cells.setdefault(cell["pins"]["Q"]["net"],[]).append(cell)
        successors={cell["name"]:[] for cell in items}
        for cell in items:
            for predecessor in q_to_cells.get(cell["pins"]["D"]["net"],[]):
                if predecessor["name"]!=cell["name"]:
                    successors[predecessor["name"]].append(cell["name"])
        groups.append({"clock_net":clock,"flipflops":[c["name"] for c in items],
                       "count":len(items),"possible_state_bits":len(items)})
        for start in items:
            seen=[start["name"]]
            while len(successors[seen[-1]])==1 and successors[seen[-1]][0] not in seen:
                seen.append(successors[seen[-1]][0])
            if len(seen)>=2 and not any(set(seen)<set(chain["cells"]) for chain in chains):
                chains.append({"clock_net":clock,"cells":seen,
                               "classification":"possible_shift_register"})
    # A 16-state TAP is not proven by 4 DFFs: transition behavior is required.
    candidates=[{"clock_net":g["clock_net"],"flipflops":g["flipflops"],
                 "classification":"possible_4_bit_state_register",
                 "tap_verified":False,
                 "missing_evidence":["16-state transition function","TMS-controlled branching",
                                     "IR/DR capture-shift-update paths","TDO mux"]}
                for g in groups if g["count"]==4]
    return {"cells":cells,"clock_groups":groups,"shift_register_candidates":chains,
            "tap_state_register_candidates":candidates,
            "automatic_tap_identification":False}
