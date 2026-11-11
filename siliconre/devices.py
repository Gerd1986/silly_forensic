"""Human-annotated MOS devices and terminal-to-trace evidence.

No transistor is inferred from pixels. The operator supplies type, location,
and trace IDs for G/D/S; bulk is optional. Only accepted trace IDs are allowed.
"""
import re
try:
    from .reconstruction import reconstruct
except ImportError:
    from reconstruction import reconstruct

_IDENT=re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

def ensure_device_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS mos_devices (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL CHECK(kind IN ('nmos','pmos')),
      layer TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
      gate_trace INTEGER NOT NULL, drain_trace INTEGER NOT NULL,
      source_trace INTEGER NOT NULL, bulk_trace INTEGER,
      note TEXT NOT NULL DEFAULT '')""")

def add_device(db,name,kind,layer,x,y,gate,drain,source,bulk=None,note=""):
    ensure_device_schema(db)
    if not _IDENT.fullmatch(name) or kind not in ("nmos","pmos"):
        raise ValueError("Invalid device name or type")
    terminals=[int(gate),int(drain),int(source)]
    if bulk is not None:
        terminals.append(int(bulk))
    for ident in terminals:
        row=db.execute("SELECT status FROM trace_candidates WHERE id=?",(ident,)).fetchone()
        if not row or row[0] != "accepted":
            raise ValueError(f"Trace #{ident} must exist and be accepted")
    db.execute("""INSERT INTO mos_devices
      (name,kind,layer,x,y,gate_trace,drain_trace,source_trace,bulk_trace,note)
      VALUES (?,?,?,?,?,?,?,?,?,?)""",
      (name,kind,layer,float(x),float(y),int(gate),int(drain),int(source),
       None if bulk is None else int(bulk),note))

def list_devices(db):
    ensure_device_schema(db)
    keys=("id","name","kind","layer","x","y","gate_trace","drain_trace","source_trace","bulk_trace","note")
    return [dict(zip(keys,row)) for row in db.execute(
        "SELECT "+",".join(keys)+" FROM mos_devices ORDER BY id")]

def resolve_devices(db):
    net_by_trace={}
    for net in reconstruct(db):
        for member in net["members"]:
            if member["kind"]=="trace":
                net_by_trace[member["id"]]=net["name"]
    result=[]
    for device in list_devices(db):
        pins={}
        for pin,col in (("g","gate_trace"),("d","drain_trace"),("s","source_trace"),("b","bulk_trace")):
            ident=device[col]
            pins[pin]=net_by_trace.get(ident) if ident is not None else None
        result.append({"name":device["name"],"kind":device["kind"],"layer":device["layer"],
                       "x":device["x"],"y":device["y"],"pins":pins,
                       "complete":all(pins[p] is not None for p in ("g","d","s")),
                       "note":device["note"]})
    return result

def spice_netlist(db):
    lines=["* SiliconRE: manually annotated MOS devices; connectivity NOT electrically verified.",
           "* Models NMOS/PMOS must be supplied by the user; no dimensions extracted."]
    for device in resolve_devices(db):
        p=device["pins"]
        if not device["complete"] or p["b"] is None:
            lines.append(f"* SKIPPED {device['name']}: incomplete terminal mapping (bulk required)")
            continue
        lines.append(f"M{device['name']} {p['d']} {p['g']} {p['s']} {p['b']} {device['kind'].upper()}")
    lines.append(".end")
    return "\n".join(lines)+"\n"
