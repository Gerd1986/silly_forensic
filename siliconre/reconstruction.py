"""Evidence-preserving multilayer connectivity and structural HDL export.

Only explicitly accepted links are edges. Spatial proximity is never treated as
electrical proof. HDL contains disconnected wire/signal declarations, no gates.
"""
import json
from netgraph import ensure_net_schema
from vias import ensure_via_schema

def ensure_schema(db):
    ensure_net_schema(db)
    ensure_via_schema(db)
    db.execute("""CREATE TABLE IF NOT EXISTS via_trace_links (
        id INTEGER PRIMARY KEY, via_id INTEGER NOT NULL, trace_id INTEGER NOT NULL,
        UNIQUE(via_id, trace_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS via_layer_links (
        id INTEGER PRIMARY KEY, via_a INTEGER NOT NULL, via_b INTEGER NOT NULL,
        UNIQUE(via_a,via_b), CHECK(via_a < via_b))""")

def associate_via_trace(db, via_id, trace_id):
    ensure_schema(db)
    via = db.execute("SELECT layer,status FROM via_candidates WHERE id=?", (via_id,)).fetchone()
    trace = db.execute("SELECT layer,status FROM trace_candidates WHERE id=?", (trace_id,)).fetchone()
    if not via or not trace or via[0] != trace[0] or via[1] != "accepted" or trace[1] != "accepted":
        raise ValueError("Via and trace must be accepted and belong to the same layer")
    db.execute("INSERT OR IGNORE INTO via_trace_links(via_id,trace_id) VALUES (?,?)",(via_id,trace_id))

def connect_vias(db, a, b):
    ensure_schema(db)
    a,b = sorted((int(a),int(b)))
    if a == b:
        raise ValueError("Choose distinct vias")
    rows = db.execute("SELECT layer,status FROM via_candidates WHERE id IN (?,?)",(a,b)).fetchall()
    if len(rows) != 2 or rows[0][0] == rows[1][0] or any(r[1] != "accepted" for r in rows):
        raise ValueError("Both vias must be accepted and belong to different layers")
    db.execute("INSERT OR IGNORE INTO via_layer_links(via_a,via_b) VALUES (?,?)",(a,b))

def reconstruct(db):
    ensure_schema(db)
    nodes = {}
    for ident,layer in db.execute("SELECT id,layer FROM trace_candidates WHERE status='accepted'"):
        nodes[("t",ident)] = {"kind":"trace","id":ident,"layer":layer}
    for ident,layer in db.execute("SELECT id,layer FROM via_candidates WHERE status='accepted'"):
        nodes[("v",ident)] = {"kind":"via","id":ident,"layer":layer}
    parent = {k:k for k in nodes}
    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    def union(a,b):
        if a in parent and b in parent:
            parent[root(a)] = root(b)
    for layer,a,b in db.execute("SELECT layer,source_id,target_id FROM candidate_links"):
        if nodes.get(("t",a),{}).get("layer")==layer and nodes.get(("t",b),{}).get("layer")==layer:
            union(("t",a),("t",b))
    for v,t in db.execute("SELECT via_id,trace_id FROM via_trace_links"):
        if nodes.get(("v",v),{}).get("layer")==nodes.get(("t",t),{}).get("layer") and ("v",v) in nodes:
            union(("v",v),("t",t))
    for a,b in db.execute("SELECT via_a,via_b FROM via_layer_links"):
        if ("v",a) in nodes and ("v",b) in nodes and nodes[("v",a)]["layer"] != nodes[("v",b)]["layer"]:
            union(("v",a),("v",b))
    groups={}
    for k in nodes:
        groups.setdefault(root(k),[]).append(nodes[k])
    ordered=sorted(groups.values(),key=lambda group: sorted((n["kind"],n["id"]) for n in group)[0])
    return [{"name":f"net_{i:04d}","members":sorted(group,key=lambda n:(n["layer"],n["kind"],n["id"]))}
            for i,group in enumerate(ordered,1)]

def export_json(db):
    return json.dumps({"electrically_verified":False,
                       "source":"manually accepted visual links only",
                       "nets":reconstruct(db)},indent=2)

def export_verilog(db):
    nets=reconstruct(db)
    lines=["// SiliconRE structural connectivity only: NO logic inferred.",
           "// Nets are based on manually accepted links; electrical continuity unverified.",
           "module siliconre_reconstruction ();"]
    lines.extend(f"  (* keep = \"true\" *) wire {n['name']};" for n in nets)
    lines.append("endmodule")
    return "\n".join(lines)+"\n"

def export_vhdl(db):
    nets=reconstruct(db)
    lines=["-- SiliconRE structural connectivity only: NO logic inferred.",
           "-- Nets are based on manually accepted links; electrical continuity unverified.",
           "library ieee;","use ieee.std_logic_1164.all;",
           "entity siliconre_reconstruction is","end entity;",
           "architecture structural of siliconre_reconstruction is"]
    lines.extend(f"  signal {n['name']} : std_logic;" for n in nets)
    lines.extend(["begin","end architecture;"])
    return "\n".join(lines)+"\n"
