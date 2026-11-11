"""Conservative manual candidate connectivity graph.

Connections are user assertions, not electrically inferred nets.
"""
def ensure_net_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS candidate_links (
        id INTEGER PRIMARY KEY,
        layer TEXT NOT NULL,
        source_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        UNIQUE(layer, source_id, target_id),
        CHECK(source_id < target_id)
    )""")

def add_link(db, layer, source_id, target_id):
    ensure_net_schema(db)
    a, b = sorted((int(source_id), int(target_id)))
    if a == b:
        raise ValueError("Select two distinct candidates")
    rows = db.execute(
        "SELECT id,status FROM trace_candidates WHERE layer=? AND id IN (?,?)",
        (layer, a, b)).fetchall()
    if len(rows) != 2 or any(status != "accepted" for _, status in rows):
        raise ValueError("Both candidates must exist in this layer and be accepted")
    db.execute("INSERT OR IGNORE INTO candidate_links(layer,source_id,target_id) VALUES (?,?,?)",
               (layer, a, b))

def list_links(db, layer):
    ensure_net_schema(db)
    return [{"id": row[0], "layer": layer, "source_id": row[1], "target_id": row[2]}
            for row in db.execute(
                "SELECT id,source_id,target_id FROM candidate_links WHERE layer=? ORDER BY id",
                (layer,))]

def connected_groups(db, layer):
    links = list_links(db, layer)
    accepted = [row[0] for row in db.execute(
        "SELECT id FROM trace_candidates WHERE layer=? AND status='accepted' ORDER BY id", (layer,))]
    parent = {i:i for i in accepted}
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for edge in links:
        a, b = edge["source_id"], edge["target_id"]
        if a in parent and b in parent:
            parent[root(a)] = root(b)
    groups = {}
    for i in accepted:
        groups.setdefault(root(i), []).append(i)
    return sorted((sorted(group) for group in groups.values()), key=lambda g:g[0])
