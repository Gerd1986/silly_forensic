"""Read-only evidence-ranked debug interface hypotheses from operator annotations.

This is NOT automatic TAP/port recognition from silicon images. Labels are
untrusted clues, not verified pin assignments. No unlocking/probing actions.
"""
import re
try:
    from .reconstruction import reconstruct
except ImportError:
    from reconstruction import reconstruct

PROTOCOLS={
 "JTAG_1149_1":{"required":("TCK","TMS","TDI","TDO"),"optional":("TRST","SRST"),
                 "description":"IEEE 1149.1 JTAG TAP"},
 "SWD":{"required":("SWCLK","SWDIO"),"optional":("SWO","NRST"),
        "description":"Arm Serial Wire Debug"},
 "CJTAG_1149_7":{"required":("TCKC","TMSC"),"optional":("TDO","TDI"),
                 "description":"IEEE 1149.7 compact JTAG"},
 "UART_DEBUG":{"required":("TX","RX"),"optional":("GND","RTS","CTS"),
               "description":"Asynchronous serial debug candidate"},
 "SPI_DEBUG":{"required":("SCLK","MOSI","MISO","CS"),"optional":("RESET",),
              "description":"SPI-like debug or test bus (function unknown)"},
 "I2C_TEST":{"required":("SCL","SDA"),"optional":("RESET",),
             "description":"I2C-like service or test interface (function unknown)"},
 "SWIM":{"required":("SWIM",),"optional":("NRST",),
         "description":"Single Wire Interface Module candidate"},
 "BDM":{"required":("BKGD",),"optional":("RESET","DSCLK","DSI","DSO"),
        "description":"Background Debug Mode candidate"},
 "NEXUS_5001":{"required":("MCKO","MSEO","MDO"),"optional":("MCKI","MDI","EVTI","EVTO"),
               "description":"IEEE-ISTO 5001 Nexus trace/debug candidate"},
}
_ALIASES={"TCK":"TCK","TMS":"TMS","TDI":"TDI","TDO":"TDO",
          "TRST":"TRST","NTRST":"TRST","SRST":"SRST","RESET":"RESET",
          "SWCLK":"SWCLK","SWDIO":"SWDIO","SWO":"SWO","NRST":"NRST",
          "TCKC":"TCKC","TMSC":"TMSC","TX":"TX","RX":"RX","GND":"GND",
          "RTS":"RTS","CTS":"CTS","SCLK":"SCLK","MOSI":"MOSI","MISO":"MISO",
          "CS":"CS","SCL":"SCL","SDA":"SDA","SWIM":"SWIM",
          "BKGD":"BKGD","DSCLK":"DSCLK","DSI":"DSI","DSO":"DSO",
          "MCKO":"MCKO","MSEO":"MSEO","MDO":"MDO","MCKI":"MCKI",
          "MDI":"MDI","EVTI":"EVTI","EVTO":"EVTO"}
def _signals(label):
    # Delimiters prevent e.g. TX in CONTEXT or TCK in NOTCK.
    return {_ALIASES[t] for t in re.findall(r"[A-Za-z][A-Za-z0-9]*",label.upper())
            if t in _ALIASES}

def analyze_debug_interfaces(db):
    try:
        rows=db.execute("SELECT layer,x,y,label FROM markers").fetchall()
    except Exception:
        rows=[]
    nets=reconstruct(db)
    # Marker association is proximity evidence only, not a confirmed electrical link.
    trace_boxes={}
    try:
        for ident,layer,x,y,w,h in db.execute(
            "SELECT id,layer,x,y,width,height FROM trace_candidates WHERE status='accepted'"):
            trace_boxes[(layer,ident)]=(x,y,w,h)
    except Exception:
        pass
    evidence={}
    for layer,x,y,label in rows:
        for signal in _signals(label or ""):
            matches=[]
            for net in nets:
                for member in net["members"]:
                    if member["kind"]!="trace" or member["layer"]!=layer:
                        continue
                    box=trace_boxes.get((layer,member["id"]))
                    if box and box[0]<=x<=box[0]+box[2] and box[1]<=y<=box[1]+box[3]:
                        matches.append(net["name"])
            evidence.setdefault(signal,[]).append(
                {"layer":layer,"x":x,"y":y,"label":label,"candidate_nets":sorted(set(matches)),
                 "electrically_verified":False})
    findings=[]
    for protocol,spec in PROTOCOLS.items():
        required=set(spec["required"])
        present=required.intersection(evidence)
        if not present:
            continue
        optional=set(spec["optional"]).intersection(evidence)
        findings.append({"protocol":protocol,"description":spec["description"],
                         "required_signals":list(spec["required"]),
                         "matched_signals":sorted(present),
                         "missing_signals":sorted(required-present),
                         "optional_signals":sorted(optional),
                         "coverage":round(len(present)/len(required),3),
                         "classification":"label_based_hypothesis",
                         "evidence":{key:evidence[key] for key in sorted(present|optional)}})
    return sorted(findings,key=lambda x:(-x["coverage"],-len(x["matched_signals"]),x["protocol"]))
